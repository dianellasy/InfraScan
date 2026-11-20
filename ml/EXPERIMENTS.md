# Experiments log

Status: b0 baseline done and shipped as v0-mvp (Milestone A).

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

## 1. b0: YOLO11s, 640, 50 epochs (11 Nov 2026)

- Config: yolo11s.pt (COCO), imgsz 640, 50 epochs, patience 15, batch 32,
  seed 42, default augmentation, AMP, 1x Tesla T4. Ultralytics 8.4.210.
- Kaggle: https://www.kaggle.com/code/atulchahar/infrascan-b0-yolo11s-640 (version 1). Total 425 min of
  GPU session (about 50 min data prep, about 7.3 min per epoch).
- Raw files: `ml/reports/b0_yolo11s_640/` (results.csv, eval_val.json,
  eval_test.json, stats.json, env.json, curves).
- Val: P 0.640, R 0.532, mAP50 0.580, mAP50-95 0.304.
- Val pothole: AP50 0.494. At conf 0.25 P 0.651 R 0.400. At 0.65 P 0.922
  R 0.089.
- Test (run once for Milestone A): mAP50 0.594, pothole AP50 0.482. At conf
  0.25 pothole P 0.645 R 0.393.
- Weakest countries (val mAP50): Norway 0.231, India 0.399, Czech 0.406.
  India transverse crack AP50 0.078.
- CPU time through `inference_service` (i5-8365U): 162 to 327 ms per image,
  first call about 2.5 s.
- Learned:
  - Pothole is the weakest class and far from the PRD target.
  - mAP50 was still rising slowly at epoch 50.
  - Data prep on GPU time wastes about 50 min per run. Data loading on 4 CPU
    cores is the bottleneck (about 69 images/s).
  - 2,118 cross-split near-duplicate pairs, so scores may be optimistic.
- Next:
  - CPU-only prep kernel with pre-resized images and duplicate-aware grouping,
    reused by every GPU run.
  - Then try imgsz 960 or 1024 (helps Norway and small potholes), a larger
    model (m), and a pothole-focused model with extra pothole data.

## 2. v1_yolo26m_1024: YOLO26m, 1024, 19 epochs (19 to 20 Nov 2026)

- Config: yolo26m.pt (COCO), imgsz 1024, 5 classes (adds D50 Manhole cover),
  Kaggle GPU T4 x2 DDP, batch 16, time=7.75 h (19 epochs), close_mosaic 3,
  hsv_v 0.5, seed 42. Plan and decisions: `ml/V1_TRAINING_PLAN.md`.
- Split: b0 block split, minus 765 val/test images with a near-duplicate in
  another split. Train 30,529, val 3,438, test 3,653.
- Speed benchmark (1024, T4 x2, full epoch): 26s 11.4 min, 26m 24.0 min,
  26l 30.4 min.
- Test: mAP50 0.651, mAP50-95 0.340. Pothole AP50 0.494, at conf 0.25 P 0.62
  R 0.43. Manhole cover AP50 0.873.
- vs b0 on 3,653 images unseen by both (classes 0-3): mAP50 0.596 vs 0.579.
- vs e7 (Adarsh, YOLO26s 640, India + US + CMIRD) on the v2 test split:
  all countries 0.596 vs 0.404, but India 0.361 vs 0.586 and US 0.675 vs 0.743.
  e7 likely trained on many of those India/US images, so its lead there is
  partly leakage. Raw: `ml/reports/v1_yolo26m_1024/h2h_v2_vs_e7.json`.
- Learned: still improving at the last epoch (undertrained). India stays weak
  without Indian data beyond RDD2022.
- Decision (20 Nov, 09:45): keep e7 live; this model goes up as a draft PR.
- Next: one shared, published split; train the bigger 1024 recipe on
  RDD2022 + CMIRD; more GPU time.
