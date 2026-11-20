# Model card: InfraScan road damage detector

Current model: **v1** (`infrascan-yolo26s-rdd2022-cmird`, run e7), in `backend/inference/weights/best.pt`
since 20 Nov 2026. The earlier v0-mvp is kept below as history.

# v1: infrascan-yolo26s-rdd2022-cmird (run e7)

Every number in this section comes from files in `ml/reports/e7_yolo26s_cmird_holdout/` (val, test,
held-out videos) or was measured as stated. How it was chosen: `ml/EXPERIMENTS.md` sections 3-10.

## What it does

Same task, classes and API output as v0: boxes for 4 classes in one road photo (ids 0 Longitudinal crack,
1 Transverse crack, 2 Alligator crack, 3 Pothole; class order unchanged, so `inference/labels.py` and stored
class ids stay valid). Only "Pothole" is one of the six PRD issue types.

## Model

- Ultralytics YOLO26s (COCO pretrained), fine-tuned. Ultralytics 8.4.210 (YOLO26 needs a recent Ultralytics;
  older versions cannot load it). Input size 640. Weights 20.3 MB.
- Training: 50 epochs, batch 32, seed 42, default augmentation, AMP, one Tesla T4 on Kaggle, 2.05 h.
  Kaggle notebook (code, full log, outputs): https://www.kaggle.com/code/adarshjaiswal2234/infrascan-e7-yolo26s-cmird-holdout
- Confidence cut-off in the app: 0.25 (`INFRASCAN_CONF`), unchanged.

## Data

- **RDD2022, India + United States only** (the PRD pilots), split 80/10/10 per country with near-duplicates kept
  in one split (data_v1, `ml/reports/data_v1/`): train 10,023 / val 1,253 / test 1,235 photos. CC BY-SA 4.0.
- **Plus CMIRD** Indian motorbike dashcam frames, train only: 202 frames / 318 pothole boxes from 14 videos.
  7 other CMIRD videos (119 frames, 164 potholes) were kept out of training to test on. CC BY-NC-SA 4.0.
- **Licence: because of CMIRD, this model is for non-commercial use only** (fine for the project and paper).
- Night is still essentially missing from the training data.

## Results

### Test split (1,235 photos, 165 with potholes, 358 pothole boxes; scored once, 20 Nov 2026)

| Test | v1 (e7) | e4 (same model without CMIRD) |
| --- | --- | --- |
| mAP50, all classes (Ultralytics) | 0.621 | 0.625 |
| Pothole AP50 (Ultralytics) | 0.466 | 0.482 |
| Pothole precision / recall at 0.25 (own matching, IoU 0.5) | 0.558 / 0.455 | 0.627 / 0.408 |
| Pothole photos found, conf 0.25 (of 165) | 118 | 114 |
| Photos wrongly flagged, conf 0.25 | 17 | 12 |
| India mAP50 / United States mAP50 | 0.415 / 0.573 | 0.415 / 0.583 |

Paired e7 minus e4 on test (own matching, 95% interval): pothole AP50 -0.004 (-0.038 to +0.033), i.e. the same;
recall at 0.25 +0.047 (+0.010 to +0.082), i.e. finds more potholes, at the cost of a few more false alarms.

For comparison, v0-mvp's test numbers below (pothole AP50 0.482) were measured on a leaky split with all 7
countries, so they are not comparable with the table above.

### Pothole at fixed cut-offs (test, own matching, IoU 0.5)

PRD target: precision at least 0.90 and recall at least 0.85.

| cut-off | precision | recall |
| --- | --- | --- |
| 0.15 | 0.429 | 0.533 |
| 0.25 (app default) | 0.558 | 0.455 |
| 0.35 | 0.678 | 0.377 |
| 0.50 | 0.788 | 0.249 |
| 0.65 | 0.829 | 0.095 |

The PRD pothole target is NOT met at any cut-off. At the app default 0.25 the model boxes about 4-5 in 10
labelled potholes, and about 4 in 10 of its pothole boxes do not match a label (many of these sit on real but
unlabelled damage, see the error review in `ml/EXPERIMENTS.md` sec. 3). Per photo ("does this photo have a
pothole?") it is better: 118 of 165 pothole photos found, 17 photos wrongly flagged.

### Indian motorbike footage (7 CMIRD videos never used in training)

| Held-out videos (119 frames, 164 potholes) | v1 (e7) | e4 |
| --- | --- | --- |
| Pothole AP50 | 0.229 | 0.080 |
| Recall at 0.25 | 0.30 | 0.02 |
| Frames with a pothole found, conf 0.20 | 94 | 12 |

This is why e7 was chosen over e4: e4 (trained on RDD2022 only) is nearly blind on this kind of footage.
Only 7 videos from one source, so read the size of the gain as rough.

## Speed (CPU, through the real `inference_service` code path)

Intel Core i7-13700HX laptop, batch 1, median of 5 calls (20 Nov 2026), same machine for both models:

| input size | v1 (e7) | v0-mvp |
| --- | --- | --- |
| 640x640 | 198 ms | 195 ms |
| 1280x720 | 142 ms | 91 ms |
| 1920x1080 | 173 ms | 87 ms |
| 4032x3024 (phone photo) | 405 ms | 238 ms |

The first call in a process takes about 3 s (model load). Well under the PRD limit of 2 s per frame.

## Severity

Unchanged from v0: HEURISTIC, NOT VALIDATED (box area share plus class, `severity_heuristic` in `service.py`).

## Known limits

- Potholes: recall about 0.45 at 0.25 on test; misses many, especially small and far ones.
- Night and low light: not in the training data.
- Motorbike / monsoon street footage: much better than e4 but still finds only about 3 in 10 potholes on
  unseen videos. More footage of that kind is the most promising next step (`ml/NEXT_DATA.md`).
- Draws some loose or oversized boxes; on video, use tracking to steady boxes (Colab notebook, step 7b).
- 1 of 6 PRD issue types covered. Non-commercial licence (CMIRD).

## Reproduce

`python ml/kaggle/build_kernel.py --run e7_yolo26s_cmird_holdout`, then
`kaggle kernels push -p ml/kaggle/_build/e7_yolo26s_cmird_holdout` (needs the Kaggle datasets listed in
`ml/kaggle/runs.json`). Score held-out videos with `ml/eval/score_holdout.py`, test with
`ml/eval/evaluate.py` (`full_report`).

---

# History: v0-mvp (infrascan-rdd2022-yolo11s)

Status: Milestone A (MVP model). Trained 11 Nov 2026. Replaced by v1 on 20 Nov 2026. Every number below comes
from files in `ml/reports/b0_yolo11s_640/`. Its scores are optimistic (split leakage, see Data).

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
  Fixed in data_v1 (14 Nov 2026, `ml/EXPERIMENTS.md` sec. 2): the same model setup on the leak-free split
  (b1) scores pothole AP50 0.37-0.40 on val instead of 0.49.

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
- Split leakage from near-duplicates makes the scores above optimistic (see Data).
- Close-up phone photos look different from the dashcam views in RDD2022.

## Reproduce

The exact code, full log and outputs of this run are in the Kaggle notebook
https://www.kaggle.com/code/atulchahar/infrascan-b0-yolo11s-640 and in git commit c4bfda1
(`ml/kaggle/b0_yolo11s_640/body.py` and the `ml/data/prepare_rdd2022.py` of that commit). The current
`prepare_rdd2022.py` only builds the leak-free v1 split, so it can no longer rebuild b0's split; the b0 job
folder was removed from `ml/kaggle/` on 18 Nov 2026. b0's numbers stay in `ml/reports/b0_yolo11s_640/`.
