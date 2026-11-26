# Model card: infrascan-yolo26m-rdd2022, v2

Trained 19 to 20 Nov 2026 (run `v1_yolo26m_1024`). Every number below comes
from files in `ml/reports/v1_yolo26m_1024/`. Previous models: v1 (e7,
YOLO26s, commit 0e1dce4) and v0-mvp (YOLO11s, `ml/MODEL_CARD.md`).

## What it does

Finds road damage in one road photo and returns boxes for 5 classes:

| id | RDD2022 code | label |
| --- | --- | --- |
| 0 | D00 | Longitudinal crack |
| 1 | D10 | Transverse crack |
| 2 | D20 | Alligator crack |
| 3 | D40 | Pothole |
| 4 | D50 | Manhole cover (new) |

Manhole cover means "a cover is there". The model cannot tell open or broken
covers. 3,553 of the 3,581 D50 boxes are from Japan, so expect weaker results
on Indian manholes.

## Model and training

- Ultralytics YOLO26m (COCO pretrained), input size 1024, 5 classes.
  Ultralytics 8.4.210. Weights: `backend/inference/weights/best.pt` (44 MB).
- Kaggle GPU T4 x2 (DDP), batch 16, `time=7.75` hours, which gave 19 epochs.
  Optimizer auto, close_mosaic 3, hsv_v 0.5, seed 42.
- Val mAP50 was still rising at the last epoch (0.643, 0.647, 0.651), so the
  model is undertrained. More GPU time is the clearest next gain.
- Code: `ml/kaggle/v1_prep` (data), `ml/kaggle/v1_bench` (speed test),
  `ml/kaggle/v1_main` + `ml/kaggle/v1_train/train_v1.py` (training and eval).

## Data

- RDD2022, all 7 countries, 38,385 labeled images, long side capped at 1024.
- Split: b0 block split (30 consecutive ids per group, stratified by country
  and pothole), then every val/test image with a dHash near-duplicate
  (distance 4 or less) in another split is dropped: 765 images.
  Result: train 30,529, val 3,438, test 3,653. 109 near-duplicate pairs remain
  across splits (b0 had 2,118).
- First attempt to merge duplicates into groups (union-find) chained 27,646
  images into one group and was thrown away. See `ml/V1_TRAINING_PLAN.md`.

## Results (test split, run once)

| | precision | recall | mAP50 | mAP50-95 |
| --- | --- | --- | --- | --- |
| val | 0.679 | 0.600 | 0.652 | 0.346 |
| test | 0.681 | 0.596 | 0.651 | 0.340 |

| class (test) | P | R | AP50 | AP50-95 |
| --- | --- | --- | --- | --- |
| Longitudinal crack | 0.658 | 0.556 | 0.611 | 0.340 |
| Transverse crack | 0.625 | 0.567 | 0.601 | 0.294 |
| Alligator crack | 0.700 | 0.593 | 0.678 | 0.355 |
| Pothole | 0.617 | 0.444 | 0.494 | 0.230 |
| Manhole cover | 0.807 | 0.821 | 0.873 | 0.482 |

Pothole at fixed thresholds (test, our matching, IoU 0.5):

| conf | precision | recall |
| --- | --- | --- |
| 0.15 | 0.493 | 0.516 |
| 0.25 | 0.624 | 0.429 |
| 0.35 | 0.708 | 0.338 |
| 0.50 | 0.829 | 0.226 |
| 0.65 | 0.908 | 0.114 |

The PRD pothole target (precision 0.90, recall 0.85) is NOT met.

Per country, test mAP50: China_MotorBike 0.864, US 0.675, Japan 0.641,
China_Drone 0.597, Czech 0.418, Norway 0.369, India 0.342.
India is still the weakest country that matters.

### Compared with v0-mvp (b0)

On 3,653 images that are in both the old and new test split (neither model
trained on them), classes 0 to 3, AP50:

| | b0 YOLO11s 640 | v2 YOLO26m 1024 |
| --- | --- | --- |
| mean | 0.579 | 0.596 |
| Longitudinal | 0.584 | 0.611 |
| Transverse | 0.579 | 0.601 |
| Alligator | 0.682 | 0.678 |
| Pothole | 0.471 | 0.494 |

### Compared with v1 (e7)

Run `ml/kaggle/h2h` on 20 Nov 2026, on the v2 test split, classes 0 to 3, AP50
(mean of the 4 classes). e7's own split is not published, so e7 has very likely
trained on many of the India and US images here (it used India + US with an
80/10/10-style split). Its India and US scores are therefore optimistic.

| images | v2 YOLO26m 1024 | v1 e7 YOLO26s 640 |
| --- | --- | --- |
| India (711) | 0.361 (pothole 0.408) | 0.586 (pothole 0.614) |
| United States (474) | 0.675 (pothole 0.486) | 0.743 (pothole 0.555) |
| India + US (1,185) | 0.622 (pothole 0.411) | 0.734 (pothole 0.610) |
| all 7 countries (3,653) | 0.596 (pothole 0.494) | 0.404 (pothole 0.335) |

Reading: v2 is much better across all countries. On India and US, e7 scores
higher, but the gap may be partly or mostly leakage. A fair answer needs one
shared, published split for both training sets.

## Speed (CPU, i5-8365U, batch 1, median of 5)

| input | 1024 (shipped) | 800 |
| --- | --- | --- |
| 1280x720 | 1,362 ms | 919 ms |
| 1920x1080 | 1,165 ms | 889 ms |

Under the PRD 2 s per frame, but about 6x slower than the 640 px models.

## Severity

Same box-area heuristic as before (HEURISTIC, NOT VALIDATED). Manhole cover
is always "minor", because the model does not see whether it is open.

## Known limits

- Pothole recall about 0.43 at confidence 0.25.
- India mAP50 0.34. No CMIRD or other Indian data in training.
- Night and rain barely in the data.
- Manhole cover is mostly learned from Japanese roads.
- Undertrained (19 epochs, still improving).
