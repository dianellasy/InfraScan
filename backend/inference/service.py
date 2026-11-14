import json
import os
from io import BytesIO
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageOps
from ultralytics import YOLO

from inference.labels import CLASS_LABELS, class_label

# Class order must match the training labels (see ml/data/prepare_rdd2022.py):
# 0 D00, 1 D10, 2 D20, 3 D40 from RDD2022. Defined in inference/labels.py so
# the API can map stored class IDs without importing the model stack.
label_map = CLASS_LABELS

# Which model made a detection. Update both when weights/best.pt changes.
MODEL_NAME = "infrascan-rdd2022-yolo11s"
MODEL_VERSION = "v0-mvp"

# Detections below this confidence are dropped. See ml/MODEL_CARD.md for the
# precision and recall you get at each threshold.
CONF_THRESHOLD = float(os.environ.get("INFRASCAN_CONF", 0.25))
IMG_SIZE = 640

# Optional lighting normalization (CLAHE). Off by default until tests show it helps.
USE_CLAHE = os.environ.get("INFRASCAN_CLAHE", "0") == "1"

INFERENCE_DIR = Path(__file__).resolve().parent
DEFAULT_WEIGHT_PATH = str(INFERENCE_DIR / "weights" / "best.pt")
DEFAULT_IMAGE_PATH = str(INFERENCE_DIR / "data" / "uploads" / "multi.png")
OUTPUT_PATH = INFERENCE_DIR / "data" / "outputs" / "annotated.jpg"

# Per-process cache: each Celery prefork child keeps its own loaded model(s).
_models: dict[str, YOLO] = {}


def get_model(model_file_path: str = DEFAULT_WEIGHT_PATH) -> YOLO:
    """Load YOLO weights once per process; reuse on later calls."""
    resolved = str(Path(model_file_path).resolve())
    model = _models.get(resolved)
    if model is None:
        model = YOLO(resolved)
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
    """Basic pre-processing before YOLO.

    Resizing is done by YOLO itself (letterbox to IMG_SIZE), so here we only
    make sure the image is RGB and, if asked, apply CLAHE on the lightness
    channel to even out lighting.
    """
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


def inspect_model(model):
    print("classes:", model.names)
    print("Task:", model.task)


def _as_predict_source(image_input, filename=None):
    """Accept a filesystem path or raw image bytes; return a pre-processed PIL image.

    EXIF orientation is applied first, so the model, its boxes, the annotated
    image, and `image_width`/`image_height` all use the upright pixel grid
    (the same oriented size the submission controller validates). YOLO
    letterboxes internally but returns boxes in this grid, not its 640 grid.
    """
    if isinstance(image_input, (bytes, bytearray)):
        image = Image.open(BytesIO(image_input))
    else:
        image = Image.open(image_input)
    image = preprocess_image(ImageOps.exif_transpose(image))
    if filename:
        image.filename = filename
    return image


def _predict(model_file_path, image_input, filename=None):
    model = get_model(model_file_path)
    source = _as_predict_source(image_input, filename=filename)
    result = model.predict(source, conf=CONF_THRESHOLD, imgsz=IMG_SIZE, verbose=False)[0]
    h, w = result.orig_shape
    return result, normalize_input(result.boxes.data, h, w)


def inference_service(model_file_path, image_input, filename=None):
    result, inference_obj = _predict(model_file_path, image_input, filename=filename)
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    result.save(filename=str(OUTPUT_PATH))
    return inference_obj


def inference_with_annotation(model_file_path, image_input, filename=None):
    """Run inference and return (result JSON, annotated JPEG bytes).

    The annotated image belongs to this invocation only and is never written
    to OUTPUT_PATH, so concurrent jobs cannot read each other's evidence.
    Encoding matches `result.save()` (cv2 JPEG of `result.plot()`).
    """
    result, inference_obj = _predict(model_file_path, image_input, filename=filename)
    encoded_ok, encoded = cv2.imencode(".jpg", result.plot())
    if not encoded_ok:
        raise RuntimeError("Could not encode the annotated image as JPEG")
    return inference_obj, encoded.tobytes()


def main():
    print("starting inference module!")
    results = inference_service(DEFAULT_WEIGHT_PATH, DEFAULT_IMAGE_PATH)
    print(json.dumps(results))
    print(type(results))


if __name__ == "__main__":
    main()
