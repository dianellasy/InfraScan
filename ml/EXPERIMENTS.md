# Experiments log

Status (19 Nov 2026): b0 baseline done and shipped as v0-mvp (Milestone A); it is still the model in
`backend/inference/weights/best.pt`. v1 work (Adarsh, branch `model/pothole-v1`): leak-free data_v1, clean
baseline b1 (3 seeds); e1, e2, e3 do not beat b1; e4 (YOLO26s) is the best on RDD2022 val; test scored once
(sec. 8); ensembles/TTA not adopted (sec. 9); e7 (e4 + CMIRD, held-out videos) fixes most of the motorbike-video
failure (sec. 10). e7 chosen and shipped as v1 on 20 Nov 2026. Runs are listed in `ml/kaggle/runs.json`.

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

## 2. data_v1: leak-free India + US split (14 Nov 2026)

- What: `ml/kaggle/data_v1` (CPU kernel, `prepare_rdd2022.py`). India + United_States only (the PRD pilot
  regions), 80/10/10 per country. Near-duplicate images are merged into one split unit before splitting, so
  confirmed duplicates never cross train/val/test (0 crossing; b0's split had 2,118 near-duplicate pairs
  across splits). Images pre-resized once; every v1 training run reuses this output, so no data prep on GPU.
- Images: train 10,023, val 1,253, test 1,235. Raw files: `ml/reports/data_v1/` (stats.json, manifest.csv).
- Data audit (one-off script `audit_rdd2022.py`, removed from ml/ on 18 Nov 2026): nothing removed, no pixel
  preprocessing. Potholes are small; night images almost absent; manholes (D50) only 28 boxes in India + US,
  so the manhole run was dropped.

## 3. b1: clean baseline, 3 seeds (14-15 Nov 2026)

- Config: same as b0 (YOLO11s, 640, 50 epochs, patience 15, batch 32, default augmentation, rdd4 classes)
  on data_v1. Seeds 42 (`b1_yolo11s_640`), 2 and 3. Tesla T4, 102-124 min per run in total.
- Kaggle: https://www.kaggle.com/code/adarshjaiswal2234/infrascan-b1-yolo11s-640 (seeds: `infrascan-b1-seed2`,
  `infrascan-b1-seed3`). Raw files: `ml/reports/b1_yolo11s_640/`, `b1_seed2/`, `b1_seed3/`.

| Val | seed 42 | seed 2 | seed 3 |
| --- | --- | --- | --- |
| mAP50 (all classes) | 0.568 | 0.568 | 0.573 |
| Pothole AP50 | 0.373 | 0.390 | 0.398 |
| Pothole P / R at conf 0.25 | 0.526 / 0.362 | 0.502 / 0.338 | 0.530 / 0.362 |
| India mAP50 | 0.353 | 0.361 | 0.348 |
| India pothole AP50 | 0.376 | 0.393 | 0.401 |
| US mAP50 | 0.562 | 0.576 | 0.589 |

- Learned:
  - Lower than b0 (pothole AP50 0.494 on b0's val) because the leak is gone, not because the model got worse.
    b0's numbers were optimistic.
  - Run-to-run spread: pothole AP50 0.373-0.398 from the seed alone. Cluster-bootstrap 95% interval for one
    run is about +/-0.06 (`ml/eval/analyze_preds.py`): with 340 val pothole boxes in 53 split units, gains
    under about 0.05 cannot be shown. US has only 11 val pothole boxes: US pothole numbers are noise.
  - Confidence cut-off: pothole F1 is flat (0.41-0.43) between 0.15 and 0.30. Leaning to 0.20 because the app
    has a human review queue. Final choice on the final model.
  - Photo-level view ("does this photo contain a pothole?"), conf 0.20: finds 66% of the 161 val photos
    with potholes; 77-80% of flagged photos really have one (all 3 seeds). Box-level recall (about 0.36-0.40)
    understates what a user sees. The PRD target (90% precision / 85% recall) does not say which is meant.
  - Error review (seed 42, conf 0.20, `ml/eval/error_sheets.py`): of 167 false alarms only 19 are in photos with
    no labels; many sit on real but unlabelled potholes or broken patches, or are drawn differently from the
    label (56 partly overlap one). Real mistakes: orange soil stains, dark shadows. Of 205 missed boxes, 122
    are under 1% of the photo and 68 were seen weakly (conf 0.05-0.2). Label noise is a large part of the error.

## 4. e1: image size 736 (15 Nov 2026)

- Change vs b1: imgsz 736 (India's native 720). Seed 42. 149 min.
- Kaggle: https://www.kaggle.com/code/adarshjaiswal2234/infrascan-e1-imgsz736. Raw files: `ml/reports/e1_imgsz736/`.
- Val: mAP50 0.568, pothole AP50 0.390, pothole P / R at 0.25: 0.509 / 0.344, India pothole AP50 0.396.
- Paired difference vs b1 seed 42: pothole AP50 +0.017 (95% interval -0.018 to +0.051), recall -0.014.
  Within the seed spread.
- Learned: a larger input alone does not help small potholes on this data. Not pursued further.

## 5. e3: pothole-only model (16 Nov 2026)

- Change vs b1: only pothole boxes kept (class set `pothole`, pothole = class 0), so cracks cannot compete.
  Seed 42. 107 min, 50 epochs.
- Kaggle: https://www.kaggle.com/code/adarshjaiswal2234/infrascan-e3-pothole-only. Raw files:
  `ml/reports/e3_pothole_only/`. Pothole is class 0 in this model (`--cls 0`).
- Val: pothole AP50 0.309, P / R at 0.25: 0.543 / 0.244, India pothole AP50 0.311.
- Paired difference vs b1 (pothole AP50): seed 42 -0.060 (95% interval -0.104 to -0.017), seed 2 -0.055
  (-0.104 to -0.005), seed 3 -0.079 (-0.117 to -0.039). Recall drops by 0.10-0.12. Worse than all three seeds.
- Photo-level at conf 0.20: finds 45% of pothole photos (b1: 66%); 85% of flagged photos correct (b1: 77-80%).
- Learned: dropping the crack labels hurts. Likely the crack boxes teach the model what damaged road looks like
  (shared features), and without them the model becomes timid on potholes. Keep the 4-class model.
  `e2_cmird_pothole` is not run.

## 6. e2: extra Indian pothole data from CMIRD (16 Nov 2026)

- Change vs b1: 301 CMIRD Indian pothole frames (460 boxes) added to train only; CMIRD frames that also show
  cracks are skipped, because CMIRD's crack boxes do not map to RDD's three crack types. Val/test unchanged.
  Built by `ml/data/prepare_cmird.py` (details and checks in `NEXT_DATA.md`); no near-duplicates of val/test.
- Seed 42. 110 min, 50 epochs. Kaggle log confirms 301 frames / 460 boxes added (71 crack frames skipped).
- Kaggle: https://www.kaggle.com/code/adarshjaiswal2234/infrascan-e2-cmird-rdd4. Raw files:
  `ml/reports/e2_cmird_rdd4/`.
- Val: mAP50 0.577, pothole AP50 0.393, P / R at 0.25: 0.554 / 0.350, India pothole AP50 0.393.
- Paired difference vs b1 (pothole AP50): seed 42 +0.011 (95% interval -0.023 to +0.046), seed 2 +0.017
  (-0.019 to +0.050), seed 3 -0.008 (-0.039 to +0.023). All within the seed spread.
- Photo-level at conf 0.20: finds 63% of pothole photos (b1: 66%); 81% of flagged photos correct (b1: 77-80%).
- Learned: 300 extra dashcam frames (about 3% more train images) are too few to move the score on this val set.
  No harm, no clear gain. Not worth the non-commercial licence restriction on its own.
- `e2_cmird_pothole` (pothole-only + CMIRD) not run, because e3 was worse than b1.
- Licence: CMIRD is CC BY-NC-SA 4.0 (non-commercial). Fine for the project and paper; a model trained on it
  must not be used commercially.

## 7. e4: YOLO26s instead of YOLO11s (16 Nov 2026)

- Change vs b1: model YOLO26s (newer family, same size). Seed 42, 50 epochs, everything else as b1; loader uses
  4 workers (unused second GPU hidden). 128 min in total, about 150 s per epoch (b1: about 120 s).
- First attempt was killed (out of RAM) after epoch 4 because images were cached in RAM (~13 GB) on top of 4
  loader workers. The cache default is now off, as in b1; the re-run is the result below.
- Kaggle: https://www.kaggle.com/code/adarshjaiswal2234/infrascan-e4-yolo26s (version 2). Raw files:
  `ml/reports/e4_yolo26s/`.
- Val: mAP50 0.601 (b1: 0.568-0.573), pothole AP50 0.422 (b1: 0.373-0.398), pothole P / R (Ultralytics'
  own operating point, not conf 0.25) 0.649 / 0.326; at conf 0.25 (own matching) 0.594 / 0.373.
- Paired difference vs b1 (pothole AP50, own matching): seed 42 +0.053 (95% interval +0.015 to +0.090), seed 2
  +0.059 (+0.029 to +0.090), seed 3 +0.034 (+0.006 to +0.065). Better than all three seeds; intervals exclude 0.
  Recall at conf 0.20 unchanged (differences -0.02 to 0.00, intervals include 0).
- Photo-level, 161 val photos with potholes:

  | conf | b1 (3 seeds) found / wrong photos | e4 found / wrong photos |
  | --- | --- | --- |
  | 0.15 | 112-114 / 37-45 | 116 / 34 |
  | 0.20 | 106 / 27-32 | 105 / 25 |
  | 0.25 | 96-97 / 19-24 | 98 / 19 |

- Learned: the newer model finds about the same pothole photos and raises fewer false alarms; its box scores
  are better on every class. It passes the rule set before the run (beats all three b1 seeds; photo-level found
  not lower - 105 vs 106 at 0.20 is one photo, and it is higher at 0.15 and 0.25). One seed only so far.

## 8. Final check on the held-back TEST split (16 Nov 2026)

- Test (1,235 images, 165 with potholes, 358 pothole boxes) scored once, locally on CPU, with
  `evaluate.full_report` (same code as val). Models: e4 and the three b1 seeds. Files: `eval_test.json` in each
  report folder.

| Test | b1 seed 42 | b1 seed 2 | b1 seed 3 | e4 YOLO26s |
| --- | --- | --- | --- | --- |
| mAP50 (all classes) | 0.593 | 0.590 | 0.598 | 0.625 |
| Pothole AP50 | 0.458 | 0.444 | 0.478 | 0.482 |
| Pothole P / R (Ultralytics) | 0.593 / 0.395 | 0.558 / 0.397 | 0.656 / 0.385 | 0.655 / 0.388 |
| Photos found / wrong photos, conf 0.20 | 118 / 30 | 122 / 32 | 117 / 22 | 117 / 23 |
| Photos found / wrong photos, conf 0.25 | 109 / 17 | 109 / 25 | 108 / 15 | 114 / 12 |

- Paired difference e4 minus b1 (pothole AP50, own matching): +0.005 (95% interval -0.029 to +0.040), +0.024
  (-0.014 to +0.063), +0.002 (-0.030 to +0.033). All intervals include 0: on test the pothole gain is NOT shown.
- Learned: e4 is at least as good as b1 on potholes, better on all classes together (+0.03 mAP50 vs every seed),
  and raises fewer false alarms at 0.25. The clear pothole gain seen on val (+0.03 to +0.06) shrinks to noise on
  test, so part of it was luck of the val set. e4 stays the candidate, as "slightly better overall", not as a
  big pothole improvement. Suggested cut-off for e4: 0.25 (114/165 photos found, 12 wrong photos).

## 9. Ensembles and test-time augmentation for e4 (18 Nov 2026, CPU, no training)

- Tool: `ml/eval/ensemble.py` (weighted box fusion of saved predictions; manual TTA, because Ultralytics turns
  `augment=True` off for YOLO26's end-to-end head). Val only; test not used. Three variants fixed before looking:
  A = e4 + the three b1 seeds; B = e4 run as is + mirrored + imgsz 768; C = B + the three b1 seeds.
  Check: the local 640 pass reproduces e4's saved Kaggle predictions exactly.
- Box-level (pothole AP50, own matching; paired vs e4, 95% interval): A +0.020 (-0.006 to +0.045),
  B +0.011 (-0.014 to +0.034), C +0.033 (+0.008 to +0.057).
- Fusion lowers confidence scores, so compare at equal false alarms (photo level, 161 val photos with potholes):

  | wrong photos allowed | e4 | A | B | C |
  | --- | --- | --- | --- | --- |
  | 10 | 84 | 71 | 78 | 74 |
  | 19 | 98 | 83 | 99 | 93 |
  | 25 | 105 | 101 | 106 | 103 |
  | 35 | 116 | 106 | 111 | 113 |

  With flags at least 90% right: e4 84 photos, B 76, C 72. At 95%: e4 61, B 48, C 66.
- Speed on the laptop CPU per photo: 0.13 s (640), 0.12 s (mirrored), 0.18 s (768); B about 0.43 s, C about 0.6 s.
- Learned: only C raises the box score beyond noise, but none finds more pothole photos than e4 alone at the same
  false-alarm level, and all are 3-5x slower. Not adopted; e4 alone stays the candidate. The usual challenge
  trick does not transfer here, likely because the b1 models are weaker than e4 and the label noise caps what
  better box placement can show.

## 10. e7: e4 + CMIRD motorbike frames, scored on held-out videos (18-19 Nov 2026)

- Why: e4 almost never finds potholes in CMIRD's Indian motorbike / monsoon dashcam frames (AP50 about 0.06,
  recall 4%), even big near ones, while it works on RDD2022-like photos. Question: is that a data gap (fixable
  with a little matching footage) or a model limit? e2 could not answer it, because it trained on all CMIRD frames.
- Change vs e4: CMIRD Indian pothole frames added to train, split by video. 7 of the 21 videos are held out and
  never trained on (every 3rd video by frame count, from the 2nd largest: 02, 04, 05, 11, 12, 20, 21 = 119
  frames, 164 potholes). Train gets 202 frames / 318 boxes from the other 14 videos (51 crack frames skipped,
  rdd4). RDD2022 val/test unchanged. Env `CMIRD_HOLDOUT` in `ml/kaggle/train/body.py`; Kaggle log confirms
  202 frames added and 119 held-out frames skipped. Seed 42, 50 epochs, 2.05 h.
- Kaggle: https://www.kaggle.com/code/adarshjaiswal2234/infrascan-e7-yolo26s-cmird-holdout. Raw files:
  `ml/reports/e7_yolo26s_cmird_holdout/` (incl. `holdout_compare_vs_e4.json`, `val_compare_vs_e4.json`).
- Scoring: `ml/eval/score_holdout.py` (held-out frames, CPU, imgsz 640; video list read from runs.json), then
  `ml/eval/ensemble.py compare --base e4 ... e7=...` with the video as the bootstrap unit. Question asked: does e7 beat
  e4 on the held-out videos without losing on RDD2022 val? (No numeric pass rule was written down before this run.)

| Held-out CMIRD videos (7 videos, 119 frames, 164 potholes) | e4 | e7 |
| --- | --- | --- |
| Pothole AP50 (own matching) | 0.080 | 0.229 |
| Pothole P / R at conf 0.25 | 0.667 / 0.024 | 0.366 / 0.299 |
| Frames with a pothole found, conf 0.20 | 12 | 94 |

  Paired e7 minus e4: AP50 +0.151 (95% interval +0.070 to +0.240), recall at 0.25 +0.264 (+0.128 to +0.358).
  Every held-out frame has a pothole, so false alarms per frame cannot be counted here; box precision falls.

| RDD2022 val | e4 | e7 |
| --- | --- | --- |
| mAP50 (all classes, Ultralytics) | 0.601 | 0.611 |
| Pothole AP50 (own matching) | 0.421 | 0.409 |
| Pothole P / R at conf 0.25 (own matching) | 0.594 / 0.373 | 0.511 / 0.403 |
| India mAP50 | 0.391 | 0.399 |
| Photos found / wrong photos, conf 0.20 (161 with potholes) | 105 / 25 | 117 / 28 |
| Photos found / wrong photos, conf 0.25 | 98 / 19 | 108 / 23 |

  Paired e7 minus e4 on val: pothole AP50 -0.011 (95% interval -0.046 to +0.026), recall at 0.25 +0.026
  (-0.007 to +0.057): no real loss.
- Learned: about 200 frames of the right kind of footage fix most of the motorbike failure (recall 2% -> 30% on
  videos never seen) without hurting RDD2022 val. The earlier failure was a data gap, not a model limit. Cost:
  more boxes overall, so a few more false alarms on RDD2022 val (precision at 0.25 0.59 -> 0.51). Caveats: only 7
  held-out videos from one source; one seed; test split not used (it was already scored once, sec. 8).
- Licence: e7 contains CMIRD (CC BY-NC-SA 4.0): fine for the project and paper, not for commercial use.
- **Chosen as v1 (20 Nov 2026)** and shipped to `backend/inference/weights/best.pt` (`MODEL_VERSION = "v1"`).
  Test split scored once after the choice (the choice used val and the held-out videos only):

| Test (1,235 photos, 165 with potholes) | e4 | e7 |
| --- | --- | --- |
| mAP50 (all classes, Ultralytics) | 0.625 | 0.621 |
| Pothole AP50 (own matching) | 0.462 | 0.457 |
| Pothole P / R at conf 0.25 (own matching) | 0.627 / 0.408 | 0.558 / 0.455 |
| Photos found / wrong photos, conf 0.25 (of 165) | 114 / 12 | 118 / 17 |

  Paired e7 minus e4: pothole AP50 -0.004 (95% interval -0.038 to +0.033), recall at 0.25 +0.047 (+0.010 to
  +0.082). Same picture as val: equal box score, more potholes found, a few more false alarms. Files:
  `ml/reports/e7_yolo26s_cmird_holdout/eval_test.json`, `test_compare_vs_e4.json`.

## 11. v2 candidate (run v1_yolo26m_1024): YOLO26m, 1024, 19 epochs (19 to 20 Nov 2026)

- Data prep for this run used the v2 version of the prep script, kept as `ml/data/prepare_rdd2022_v2.py` (it adds `--manhole`, `--dup-groups`, `--max-side`, `--limit` and `resplit_existing`). Its Kaggle kernels (`ml/kaggle/v1_*`) expect it as `prepare_rdd2022.py`, so copy it under that name when rebuilding them. Not the deployed model: the app ships v1 (e7).


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
