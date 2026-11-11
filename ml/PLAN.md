# InfraScan ML plan

Owner: Atul Chahar (model training and inference)
Started: 11 Nov 2026
Branch: `model/rdd2022-yolo11` (from `backend-dev`)

## Goal

Give the backend a road damage detector that Huy can plug into the Celery worker
without changing the inference contract. Potholes are the MVP class.

1. Milestone A (v0, MVP): a usable YOLO11 model trained on RDD2022, wired into
   `backend/inference/service.py`, before the MVP demo on 13 Nov 2026.
2. Milestone B (v1): the best model from the experiment loop, tested once on
   the held-out test split, with a full model card.

## Classes for v1

Fixed order, same as the training labels:

| id | code | label |
| --- | --- | --- |
| 0 | D00 | Longitudinal crack |
| 1 | D10 | Transverse crack |
| 2 | D20 | Alligator crack |
| 3 | D40 | Pothole |

Only the pothole class overlaps with the six PRD issue types. The MVP only needs
potholes, so v1 can serve the MVP. v1 will NOT meet the PRD goal of "classify at
least five road issue categories". Open manholes, water clogging, broken
footpaths, speed breakers and encroachments have no training data in RDD2022.
See `NEXT_DATA.md` for candidate datasets.

## Dataset

RDD2022 (Road Damage Detection 2022). Expected subsets: Japan, India, Czech,
Norway, United States, China MotorBike, China Drone. Labels are Pascal VOC XML.

Facts from the real data on Kaggle (b0 run, `ml/reports/b0_yolo11s_640/stats.json`):

- Kaggle dataset: `gurunathan88/rdd2022` (mirror says MIT). The official
  RDD2022 licence is CC BY-SA 4.0, so we follow that.
- 38,385 labeled images. Official test folders have images but 0 labels.
- Countries and image sizes:

| country | images | size | images with pothole |
| --- | --- | --- | --- |
| China_Drone | 2,401 | 512x512 | 64 |
| China_MotorBike | 1,977 | 512x512 | 164 |
| Czech | 2,829 | 600x600 | 154 |
| India | 7,706 | 720x720 | 1,530 |
| Japan | 10,506 | mostly 600x600 | 1,390 |
| Norway | 8,161 | about 4000x2000 | 256 |
| United_States | 4,805 | 640x640 | 116 |

- Boxes: D00 26,016, D10 11,830, D20 10,616, D40 (pothole) 6,544. 3,674
  images contain a pothole.
- Other label codes dropped: D44 5,057, D50 3,581, Repair 1,046, D43 793,
  D01 179, D11 45, Block crack 3, D0w0 1.
- Day vs night: only 111 images have mean brightness below 60. Brightness
  1st percentile is 67, median 128. Night data is essentially missing.

## Split plan

- Make our own train / val / test split (about 80 / 10 / 10) from the labeled
  training images only.
- Stratify by country and by "has pothole" so every split has potholes.
- Group near-duplicate frames so that frames from the same road or video never
  land in two splits. Rule used in v0: blocks of 30 consecutive image ids per
  country form one group. This was not enough: a dHash check still found 2,118
  near-duplicate pairs across splits. Next version: also merge dHash
  near-duplicates into the same group.
- The test split is touched only once per milestone. All tuning uses val.
- Scripts live in `ml/data/` so the split can be rebuilt exactly (fixed seed).

## Experiments

1. Baseline: YOLO11s, COCO pretrained, imgsz 640, about 50 epochs, patience 15,
   default augmentation, 4 classes. This feeds Milestone A.
2. Size: YOLO11n vs s vs m on the same split. Record accuracy and CPU time per
   image (batch 1).
3. Image size: 640 vs 960 or 1024.
4. Pothole focus: 4-class model vs pothole-only model. Try extra public pothole
   data (including night or low light) and score on the SAME RDD2022 val split.
5. Fix weak spots that the per-class and per-country numbers point to. For night
   images: brightness and contrast augmentation, and CLAHE pre-processing.

Training runs on Kaggle GPU through the Kaggle CLI. Seed fixed, Ultralytics
version pinned and recorded. Budget: about 20 Kaggle GPU hours in total.

## Success metrics

- Per class: precision, recall, AP50, AP50-95. Per country as well.
- Mean IoU and recall per damage class (listed in the repo README).
- Pothole precision and recall at confidence 0.25, 0.35, 0.5, 0.65.
- CPU time per image through the real code path, batch 1.

Targets:

- PRD: pothole precision at least 90% and recall at least 85%. This is a high
  bar. Published RDD2022 results are usually well below it. We will report the
  real numbers and the gap, not assume we reach it.
- PRD: under 2 seconds per frame, under 5 seconds end to end per image.
- Rough working target: overall mAP50 around 0.60 and F1 around 0.60 on our val
  split, with no class near zero recall.

## Severity

RDD2022 has no severity labels, so severity cannot be learned. v0 and v1 use a
rule based on box area as a share of the image, plus class. It is labelled
"heuristic, not validated" and lives in one small function in `service.py`.

## Risks

- Night and rain images are rare or missing in RDD2022.
- The 90% precision / 85% recall pothole target may not be reachable.
- Near-duplicate frames can leak between splits and inflate scores.
- Only 1 of 6 PRD issue types is covered.
- Kaggle GPU quota and session time limits.

## Local setup notes

- Repo at `D:\InfraScan`. D: had about 79 GB free on 11 Nov 2026.
- Windows venv at `ml\.venv-win` (Python 3.13, torch 2.15.0+cpu, ultralytics
  8.4.210, Kaggle CLI 2.2.5). The Docker worker uses Python 3.12.
- The committed `backend/.venv` is a Linux venv and is ignored here.
