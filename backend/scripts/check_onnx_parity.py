"""Compare the ONNX Runtime detector with ultralytics on real photos.

Usage (needs ultralytics, which the deployed app does not):
    python scripts/check_onnx_parity.py path/to/best.pt path/to/photos/*.jpg

Matches each ultralytics box to the ONNX box of the same class with the highest
IoU and reports the worst IoU and confidence gaps.
"""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from inference import service  # noqa: E402


def iou(a, b):
    x1, y1 = max(a[0], b[0]), max(a[1], b[1])
    x2, y2 = min(a[2], b[2]), min(a[3], b[3])
    inter = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union else 0.0


def main() -> int:
    from ultralytics import YOLO

    pt_path, photos = sys.argv[1], sys.argv[2:]
    reference = YOLO(pt_path)
    worst_iou, worst_conf, count_mismatch = 1.0, 0.0, 0
    for photo in photos:
        image = service._load_image(photo)
        ref = reference.predict(image, conf=service.CONF_THRESHOLD, imgsz=service.IMG_SIZE, verbose=False)[0]
        ref_boxes = ref.boxes.data.cpu().numpy()
        onnx_boxes = service.get_model().predict(image)
        if len(ref_boxes) != len(onnx_boxes):
            count_mismatch += 1
        for box in ref_boxes:
            same = [b for b in onnx_boxes if int(b[5]) == int(box[5])]
            if not same:
                worst_iou = 0.0
                continue
            best = max(same, key=lambda b: iou(box, b))
            worst_iou = min(worst_iou, iou(box, best))
            worst_conf = max(worst_conf, abs(float(best[4]) - float(box[4])))
        print(f"{Path(photo).name}: ultralytics {len(ref_boxes)} boxes, onnx {len(onnx_boxes)} boxes")
    print(f"worst matched IoU {worst_iou:.3f}, worst confidence gap {worst_conf:.3f}, "
          f"photos with a different box count {count_mismatch}/{len(photos)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
