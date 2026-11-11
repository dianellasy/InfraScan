# Model card: infrascan-rdd2022-yolo11s, v0-mvp

Status: Milestone A (MVP model). Trained 11 Nov 2026. Every number below comes
from files in `ml/reports/b0_yolo11s_640/`.

## What it does

Finds road damage in a single road photo and returns boxes for 4 classes:

| id | RDD2022 code | label returned by the API |
| --- | --- | --- |
| 0 | D00 | Longitudinal crack |
| 1 | D10 | Transverse crack |
| 2 | D20 | Alligator crack |
| 3 | D40 | Pothole |

Only "Pothole" matches one of the six PRD issue types. This model does NOT
meet the PRD goal of "classify at least five road issue categories". Manholes,
water clogging, footpaths, speed breakers and encroachments are not covered.
See `ml/NEXT_DATA.md` for datasets that could add them.

## Model

- Ultralytics YOLO11s (COCO pretrained), fine-tuned. Ultralytics 8.4.210.
- Input size 640. Weights: `backend/inference/weights/best.pt` (19.2 MB).
- Training: 50 epochs, batch 32, seed 42, default augmentation, AMP, on one
  Tesla T4 on Kaggle (torch 2.11.0+cu130). The whole Kaggle run took 425 min,
  including about 50 min of data prep.
- Kaggle notebook (training code, full log and outputs): https://www.kaggle.com/code/atulchahar/infrascan-b0-yolo11s-640
- Best epoch is picked by Ultralytics fitness on val. Val mAP50 was still
  rising slowly at epoch 50 (0.579), so more epochs may help a little.

## Data

- RDD2022, all countries, from the Kaggle mirror `gurunathan88/rdd2022`. The
  mirror says MIT, but the official RDD2022 release is CC BY-SA 4.0. Treat it
  as CC BY-SA 4.0 (credit the authors, share changes under the same licence).
- 38,385 labeled images. The official test folders have no labels, so all
  scores use our own split of the labeled images.
- Split 80/10/10 by blocks of 30 consecutive image ids per country, stratified
  by country and "has pothole", seed 42. Built by `ml/data/prepare_rdd2022.py`.

| split | images | images with pothole | pothole boxes |
| --- | --- | --- | --- |
| train | 30,529 | 2,953 | 5,248 |
| val | 3,808 | 362 | 663 |
| test | 4,048 | 359 | 633 |

- Dropped label codes (not in our 4 classes): D44 5,057, D50 3,581, Repair
  1,046, D43 793, D01 179, D11 45, others 4.
- Night gap: only 111 of 38,385 images have a mean brightness below 60 (out
  of 255). The 1st percentile of brightness is 67. The model has basically
  never seen night images.
- Leakage warning: a near-duplicate check (dHash, distance 4 or less) found
  2,118 image pairs that sit in different splits (1,056 test-train, 944
  train-val, 118 test-val). Val and test scores may be somewhat optimistic.
  To fix in the next data version.

## Results

### Overall (Ultralytics val, IoU 0.5 for mAP50)

| split | precision | recall | mAP50 | mAP50-95 |
| --- | --- | --- | --- | --- |
| val | 0.640 | 0.532 | 0.580 | 0.304 |
| test (run once) | 0.655 | 0.542 | 0.594 | 0.315 |

### Per class (test split)

| class | precision | recall | AP50 | AP50-95 |
| --- | --- | --- | --- | --- |
| Longitudinal crack | 0.656 | 0.559 | 0.605 | 0.348 |
| Transverse crack | 0.639 | 0.572 | 0.597 | 0.312 |
| Alligator crack | 0.706 | 0.626 | 0.692 | 0.371 |
| Pothole | 0.617 | 0.412 | 0.482 | 0.228 |

### Pothole at fixed confidence thresholds (our own matching, IoU 0.5)

PRD target: precision at least 0.90 and recall at least 0.85.

| threshold | val precision | val recall | test precision | test recall |
| --- | --- | --- | --- | --- |
| 0.25 | 0.651 | 0.400 | 0.645 | 0.393 |
| 0.35 | 0.774 | 0.311 | 0.761 | 0.311 |
| 0.50 | 0.853 | 0.201 | 0.842 | 0.210 |
| 0.65 | 0.922 | 0.089 | 0.946 | 0.111 |

The PRD pothole target is NOT met. Precision reaches 0.90 only at threshold
0.65, and then the model finds about 1 pothole in 10. At the default 0.25 it
finds about 4 in 10, and about 1 in 3 of its pothole boxes is wrong. Mean IoU
of correct pothole boxes is about 0.75.

The default threshold in `service.py` is 0.25 (best pothole F1 of the four
thresholds on val). Change it with the env var `INFRASCAN_CONF`.

### Mean IoU and recall per class at threshold 0.25 (test)

| class | recall | mean IoU of correct boxes |
| --- | --- | --- |
| Longitudinal crack | 0.527 | 0.785 |
| Transverse crack | 0.569 | 0.764 |
| Alligator crack | 0.612 | 0.769 |
| Pothole | 0.393 | 0.751 |

### Per country (test, mAP50)

| country | images | mAP50 | pothole AP50 |
| --- | --- | --- | --- |
| China_MotorBike | 240 | 0.896 | 0.920 |
| China_Drone | 270 | 0.699 | 0.742 |
| United_States | 485 | 0.654 | 0.425 |
| Japan | 1,077 | 0.556 | 0.543 |
| Czech | 307 | 0.461 | 0.177 |
| India | 799 | 0.384 | 0.430 |
| Norway | 870 | 0.308 | 0.251 |

India matters most for this project and is one of the weak countries. India
transverse cracks are very poor (AP50 0.10). Norway images are very large
(about 4000x2000) and get shrunk to 640, so small damage is lost.

## Speed (CPU, through the real `inference_service` code path)

Intel Core i5-8365U laptop, 8 threads, batch 1, median of 5 calls:

| input size | time per image |
| --- | --- |
| 640x640 | 229 ms |
| 1280x720 | 162 ms |
| 1920x1080 | 184 ms |
| 4000x2000 | 327 ms |

The first call in a process takes about 2.5 s because the model loads. The
PRD limit of 2 s per frame is met on this laptop.

## Severity

Each detection has a `severity` of minor, moderate or critical. This is a
HEURISTIC, NOT VALIDATED: a rule on box area as a share of the image plus the
class (`severity_heuristic` in `service.py`). RDD2022 has no severity labels.

## Known limits

- Potholes: low recall (about 0.40 at 0.25). Will miss many potholes.
- Night, rain and low-light images: not in the training data. Expect worse
  results.
- 1 of 6 PRD issue types covered.
- Possible split leakage from near-duplicates (see Data).
- Close-up phone photos look different from the dashcam views in RDD2022.

## Reproduce

1. `python ml/kaggle/build_kernel.py ml/kaggle/b0_yolo11s_640/body.py ml/kaggle/b0_yolo11s_640/run.py`
2. `kaggle kernels push -p ml/kaggle/b0_yolo11s_640 --accelerator NvidiaTeslaT4`
3. Download outputs with `kaggle kernels output atulchahar/infrascan-b0-yolo11s-640 -p ml/runs/b0_yolo11s_640`.
