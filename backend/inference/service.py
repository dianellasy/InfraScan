import json
from io import BytesIO
from pathlib import Path

from PIL import Image
from ultralytics import YOLO

label_map = {
    0: "Longtitudnal cracks",
    1: "Transverse crack",
    2: "Alligator crack",
    3: "potholes",
}

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


def normalize_input(data, h, w):
    detections = []

    for datum in data:
        detections.append({
            "class_ids": int(datum[5]),
            "label": label_map.get(int(datum[5]), f"class_{int(datum[5])}"),
            "confidence": float(datum[4]),
            "bbox_xyxy": datum[:4].tolist(),
        })

    return {
        "image_width": w,
        "image_height": h,
        "detections": detections,
    }


def inspect_model(model):
    print("classes:", model.names)
    print("Task:", model.task)


def _as_predict_source(image_input, filename=None):
    """Accept a filesystem path or raw image bytes for YOLO.predict."""
    if isinstance(image_input, (bytes, bytearray)):
        image = Image.open(BytesIO(image_input))
        if filename:
            image.filename = filename
        return image
    return image_input


def inference_service(model_file_path, image_input, filename=None):
    model = get_model(model_file_path)
    source = _as_predict_source(image_input, filename=filename)
    result = model.predict(source, conf=0.25)[0]
    h, w = result.orig_shape
    inference_obj = normalize_input(result.boxes.data, h, w)
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    result.save(filename=str(OUTPUT_PATH))
    return inference_obj


def main():
    print("starting inference module!")
    results = inference_service(DEFAULT_WEIGHT_PATH, DEFAULT_IMAGE_PATH)
    print(json.dumps(results))
    print(type(results))


if __name__ == "__main__":
    main()
