# Experiments log

Status: setup done, waiting on Kaggle CLI login before the first run.

One entry per run: date, config, Kaggle run link, key metrics, what we learned,
next step. Numbers here only come from real logs saved under `ml/reports/`.

## 0. Stub check (11 Nov 2026)

- What: ran the existing `backend/inference/service.py` with the old stub
  `weights/best.pt` (about 5.5 MB) on `data/uploads/multi.png`, CPU, Windows venv.
- Model classes in the stub: Longitudinal Cracks, Transverse Cracks,
  Alligator Cracks, Potholes (ids 0 to 3).
- Result: 3 pothole boxes (confidence 0.54, 0.43, 0.36). Model forward pass
  104 ms at 448x640. The first call took about 9 s in total because of model
  load and warm-up.
- Learned: the code path works on Windows CPU. The contract returns
  `image_width`, `image_height`, `detections[{class_ids, label, confidence, bbox_xyxy}]`.
- Next: Kaggle login, then data check and the YOLO11s baseline.
