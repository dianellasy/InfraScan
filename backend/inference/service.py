"""Road-damage inference with ONNX Runtime.

The model is the v1 YOLO26s detector exported to ONNX (`weights/best.onnx`).
ONNX Runtime replaces ultralytics/torch so the worker fits in a Vercel Python
function (about 60 MB of runtime instead of about 1 GB) and cold-starts fast.

Pre- and post-processing reproduce ultralytics 8.4 `predict()` for this model:
letterbox the long side to 640 px with gray (114) padding up to a multiple of
32, RGB scaled to 0..1, then class-aware NMS at IoU 0.5, keeping at most 300
boxes above CONF_THRESHOLD.
`scripts/check_onnx_parity.py` compares the two on real photos.
"""

import json
import os
import tempfile
import threading
from io import BytesIO
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageOps

from inference.labels import CLASS_LABELS, class_label

# Class order must match the training labels (see ml/data/prepare_rdd2022.py):
# 0 D00, 1 D10, 2 D20, 3 D40 from RDD2022. Defined in inference/labels.py so
# the API can map stored class IDs without importing the model stack.
label_map = CLASS_LABELS

# Which model made a detection. Update both when weights/best.onnx changes.
# v1 = run e7 (YOLO26s, RDD2022 India+US + CMIRD Indian motorbike frames), see ml/MODEL_CARD.md.
MODEL_NAME = "infrascan-yolo26s-rdd2022-cmird"
MODEL_VERSION = "v1"

# Detections below this confidence are dropped. See ml/MODEL_CARD.md for the
# precision and recall you get at each threshold.
CONF_THRESHOLD = float(os.environ.get("INFRASCAN_CONF", 0.25))
IOU_THRESHOLD = 0.5  # ultralytics 8.4 predict() default (validation uses 0.7)
MAX_DETECTIONS = 300  # ultralytics predict() default
IMG_SIZE = 640
STRIDE = 32  # largest model stride; dynamic-shape exports accept any multiple
PAD_VALUE = 114

# Optional lighting normalization (CLAHE). Off by default until tests show it
# helps. Needs opencv, which is not installed in the deployed worker.
USE_CLAHE = os.environ.get("INFRASCAN_CLAHE", "0") == "1"

INFERENCE_DIR = Path(__file__).resolve().parent
DEFAULT_WEIGHT_PATH = str(INFERENCE_DIR / "weights" / "best.onnx")
DEFAULT_IMAGE_PATH = str(INFERENCE_DIR / "data" / "uploads" / "multi.png")
# Only the smoke-test task writes here. Vercel's code directory is read-only.
OUTPUT_PATH = (
    Path(tempfile.gettempdir()) / "infrascan" / "annotated.jpg"
    if os.environ.get("VERCEL")
    else INFERENCE_DIR / "data" / "outputs" / "annotated.jpg"
)

# Same colors for the same class on every evidence image.
_CLASS_COLORS = {0: (255, 56, 56), 1: (255, 157, 151), 2: (255, 112, 31), 3: (72, 249, 10)}


class OnnxDetector:
    """One ONNX Runtime session for a detection model with a fixed square input."""

    def __init__(self, model_file_path: str) -> None:
        import onnxruntime as ort

        options = ort.SessionOptions()
        options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        threads = int(os.environ.get("INFRASCAN_ORT_THREADS", "0"))
        if threads > 0:
            options.intra_op_num_threads = threads
        self.session = ort.InferenceSession(
            model_file_path, sess_options=options, providers=["CPUExecutionProvider"]
        )
        model_input = self.session.get_inputs()[0]
        self.input_name = model_input.name
        side = model_input.shape[-1]
        # A fixed-shape export needs a square input; a dynamic one gets rect padding.
        self.img_size = side if isinstance(side, int) else IMG_SIZE
        self.stride = None if isinstance(side, int) else STRIDE
        meta = self.session.get_modelmeta().custom_metadata_map
        self.end2end = meta.get("end2end", "False") == "True"

    def predict(self, image: Image.Image, conf: float = CONF_THRESHOLD) -> np.ndarray:
        """Return an (N, 6) array of [x1, y1, x2, y2, confidence, class_id] in image pixels."""
        tensor, gain, pad_x, pad_y = letterbox(image, self.img_size, self.stride)
        output = self.session.run(None, {self.input_name: tensor})[0][0]
        if self.end2end:
            # (max_det, 6): boxes already xyxy and de-duplicated.
            boxes = output[output[:, 4] > conf]
        else:
            boxes = decode_and_nms(output, conf, IOU_THRESHOLD, MAX_DETECTIONS)
        if boxes.size == 0:
            return np.zeros((0, 6), dtype=np.float32)
        boxes = boxes.astype(np.float32, copy=True)
        boxes[:, [0, 2]] = (boxes[:, [0, 2]] - pad_x) / gain
        boxes[:, [1, 3]] = (boxes[:, [1, 3]] - pad_y) / gain
        width, height = image.size
        boxes[:, [0, 2]] = boxes[:, [0, 2]].clip(0, width)
        boxes[:, [1, 3]] = boxes[:, [1, 3]].clip(0, height)
        return boxes


def letterbox(
    image: Image.Image, size: int, stride: int | None = STRIDE
) -> tuple[np.ndarray, float, int, int]:
    """Resize keeping aspect ratio and pad, as ultralytics `LetterBox` does.

    With `stride`, padding stops at the next multiple of the stride (ultralytics
    "rect" inference, what `predict()` does for a .pt model); without it the
    image is padded to `size` x `size`. Returns NCHW float32 0..1 plus the gain
    and the left/top padding needed to map boxes back.
    """
    pixels = np.asarray(image, dtype=np.uint8)
    height, width = pixels.shape[:2]
    gain = min(size / height, size / width)
    new_w, new_h = round(width * gain), round(height * gain)
    pad_w, pad_h = size - new_w, size - new_h
    if stride:
        pad_w, pad_h = pad_w % stride, pad_h % stride
    pad_x, pad_y = pad_w / 2, pad_h / 2
    if (new_w, new_h) != (width, height):
        pixels = resize_bilinear(pixels, new_w, new_h)
    top, bottom = round(pad_y - 0.1), round(pad_y + 0.1)
    left, right = round(pad_x - 0.1), round(pad_x + 0.1)
    canvas = np.full((new_h + top + bottom, new_w + left + right, 3), PAD_VALUE, dtype=np.float32)
    canvas[top : top + new_h, left : left + new_w] = pixels
    tensor = canvas.transpose(2, 0, 1)[None] / 255.0
    return np.ascontiguousarray(tensor, dtype=np.float32), gain, left, top


def resize_bilinear(pixels: np.ndarray, new_w: int, new_h: int) -> np.ndarray:
    """Bilinear resize with half-pixel centers and no antialiasing (OpenCV INTER_LINEAR)."""
    height, width = pixels.shape[:2]

    def axis(new: int, old: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        src = (np.arange(new, dtype=np.float32) + 0.5) * (old / new) - 0.5
        src = np.clip(src, 0, old - 1)
        low = np.floor(src).astype(np.int64)
        high = np.minimum(low + 1, old - 1)
        return low, high, (src - low).astype(np.float32)

    y0, y1, wy = axis(new_h, height)
    x0, x1, wx = axis(new_w, width)
    data = pixels.astype(np.float32)
    rows = data[y0] * (1 - wy)[:, None, None] + data[y1] * wy[:, None, None]
    out = rows[:, x0] * (1 - wx)[None, :, None] + rows[:, x1] * wx[None, :, None]
    return np.clip(np.rint(out), 0, 255)


def decode_and_nms(raw: np.ndarray, conf: float, iou: float, max_det: int) -> np.ndarray:
    """Raw YOLO head output (4 + classes, anchors) to NMS-filtered [x1,y1,x2,y2,conf,cls]."""
    preds = raw.T  # (anchors, 4 + classes)
    scores = preds[:, 4:]
    class_ids = scores.argmax(axis=1)
    confidences = scores[np.arange(len(scores)), class_ids]
    keep = confidences > conf
    if not keep.any():
        return np.zeros((0, 6), dtype=np.float32)
    cx, cy, w, h = preds[keep, :4].T
    boxes = np.stack([cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2], axis=1)
    confidences, class_ids = confidences[keep], class_ids[keep]

    # Class-aware NMS: offset boxes per class so different classes never overlap.
    offsets = class_ids[:, None].astype(np.float32) * 7680.0
    selected = _nms(boxes + offsets, confidences, iou)[:max_det]
    return np.concatenate(
        [boxes[selected], confidences[selected, None], class_ids[selected, None].astype(np.float32)],
        axis=1,
    )


def _nms(boxes: np.ndarray, scores: np.ndarray, iou_threshold: float) -> np.ndarray:
    order = scores.argsort()[::-1]
    areas = (boxes[:, 2] - boxes[:, 0]) * (boxes[:, 3] - boxes[:, 1])
    keep: list[int] = []
    while order.size:
        best = order[0]
        keep.append(int(best))
        rest = order[1:]
        xx1 = np.maximum(boxes[best, 0], boxes[rest, 0])
        yy1 = np.maximum(boxes[best, 1], boxes[rest, 1])
        xx2 = np.minimum(boxes[best, 2], boxes[rest, 2])
        yy2 = np.minimum(boxes[best, 3], boxes[rest, 3])
        inter = np.clip(xx2 - xx1, 0, None) * np.clip(yy2 - yy1, 0, None)
        overlap = inter / (areas[best] + areas[rest] - inter + 1e-9)
        order = rest[overlap <= iou_threshold]
    return np.asarray(keep, dtype=np.int64)


# Per-process cache: each worker process (or warm Vercel instance) loads the model once.
_models: dict[str, OnnxDetector] = {}
_models_lock = threading.Lock()


def get_model(model_file_path: str = DEFAULT_WEIGHT_PATH) -> OnnxDetector:
    """Load the ONNX model once per process; reuse on later calls."""
    resolved = str(Path(model_file_path).resolve())
    with _models_lock:
        model = _models.get(resolved)
        if model is None:
            model = OnnxDetector(resolved)
            _models[resolved] = model
    return model


def severity_heuristic(class_id: int, box_area_fraction: float) -> str:
    """HEURISTIC, NOT VALIDATED. RDD2022 has no severity labels.

    Severity from the box area as a share of the whole image, plus the class.
    Change the cut points here only; nothing else depends on them.
    """
    if class_id == 3:  # pothole
        if box_area_fraction < 0.01:
            return "minor"
        if box_area_fraction < 0.04:
            return "moderate"
        return "critical"
    if class_id == 2:  # alligator crack
        if box_area_fraction < 0.03:
            return "minor"
        if box_area_fraction < 0.10:
            return "moderate"
        return "critical"
    # longitudinal and transverse cracks
    return "minor" if box_area_fraction < 0.05 else "moderate"


def preprocess_image(image: Image.Image, clahe: bool = USE_CLAHE) -> Image.Image:
    """Make the image RGB and, if asked, apply CLAHE on the lightness channel."""
    image = image.convert("RGB")
    if not clahe:
        return image
    import cv2

    lab = cv2.cvtColor(np.asarray(image), cv2.COLOR_RGB2LAB)
    l_chan, a_chan, b_chan = cv2.split(lab)
    l_chan = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(l_chan)
    rgb = cv2.cvtColor(cv2.merge((l_chan, a_chan, b_chan)), cv2.COLOR_LAB2RGB)
    return Image.fromarray(rgb)


def normalize_input(data, h, w):
    detections = []

    for datum in data:
        class_id = int(datum[5])
        x1, y1, x2, y2 = (float(v) for v in datum[:4])
        area_fraction = max(0.0, x2 - x1) * max(0.0, y2 - y1) / float(w * h) if w and h else 0.0
        label = class_label(class_id)
        detections.append({
            "class_ids": class_id,
            "label": label,
            "classification": label,
            "severity": severity_heuristic(class_id, area_fraction),
            "confidence": float(datum[4]),
            "bbox_xyxy": [x1, y1, x2, y2],
        })

    return {
        "image_width": w,
        "image_height": h,
        "model_name": MODEL_NAME,
        "model_version": MODEL_VERSION,
        "detections": detections,
    }


def _load_image(image_input) -> Image.Image:
    """Accept a filesystem path or raw image bytes; return an upright, pre-processed RGB image."""
    if isinstance(image_input, (bytes, bytearray)):
        image = Image.open(BytesIO(image_input))
    else:
        image = Image.open(image_input)
    # Boxes, annotations and camera intrinsics all use the upright image frame.
    return preprocess_image(ImageOps.exif_transpose(image))


def _predict(model_file_path, image_input):
    image = _load_image(image_input)
    boxes = get_model(model_file_path).predict(image)
    width, height = image.size
    return image, boxes, normalize_input(boxes, height, width)


def _font(size: int) -> ImageFont.ImageFont:
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # Pillow < 10.1 has no sized default font
        return ImageFont.load_default()


def draw_detections(image: Image.Image, boxes: np.ndarray) -> Image.Image:
    """Draw labelled boxes, in the style of ultralytics `Results.plot()`."""
    annotated = image.copy()
    draw = ImageDraw.Draw(annotated)
    line = max(round(sum(annotated.size) / 2 * 0.003), 2)
    font = _font(max(line * 6, 12))
    for x1, y1, x2, y2, conf, cls in boxes.tolist():
        color = _CLASS_COLORS.get(int(cls), (255, 56, 56))
        draw.rectangle((x1, y1, x2, y2), outline=color, width=line)
        text = f"{class_label(int(cls))} {conf:.2f}"
        left, top, right, bottom = draw.textbbox((0, 0), text, font=font)
        text_w, text_h = right - left, bottom - top + line
        label_top = y1 - text_h - line if y1 - text_h - line >= 0 else y1
        draw.rectangle((x1, label_top, x1 + text_w + 2 * line, label_top + text_h + line), fill=color)
        draw.text((x1 + line, label_top + line / 2), text, fill=(255, 255, 255), font=font)
    return annotated


def _jpeg_bytes(image: Image.Image) -> bytes:
    buffer = BytesIO()
    image.save(buffer, format="JPEG", quality=90)
    return buffer.getvalue()


def inference_service(model_file_path, image_input, filename=None):
    image, boxes, inference_obj = _predict(model_file_path, image_input)
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_bytes(_jpeg_bytes(draw_detections(image, boxes)))
    return inference_obj


def inference_with_annotation(model_file_path, image_input, filename=None):
    """Run inference and return (result JSON, annotated JPEG bytes).

    The annotated image belongs to this invocation only and is never written
    to OUTPUT_PATH, so concurrent jobs cannot read each other's evidence.
    """
    image, boxes, inference_obj = _predict(model_file_path, image_input)
    return inference_obj, _jpeg_bytes(draw_detections(image, boxes))


def main():
    print("starting inference module!")
    results = inference_service(DEFAULT_WEIGHT_PATH, DEFAULT_IMAGE_PATH)
    print(json.dumps(results))


if __name__ == "__main__":
    main()
