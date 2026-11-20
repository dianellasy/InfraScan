# v1 training plan: one run, best model by 10:00 on 20 Nov 2026

Written 19 Nov 2026, 22:30 IST. About 11.5 hours left.

## Decision (19 Nov, 22:35): Kaggle free GPU

- Kaggle's `NvidiaTeslaT4` accelerator is **GPU T4 x2**. b0 used only one of
  the two GPUs. v1 trains with DDP on both, which roughly doubles speed.
- Data prep moved to a CPU kernel (`ml/kaggle/v1_prep`, output
  `rdd2022_v1.tar`), so the GPU run starts training within minutes. JPEGs are
  decoded at reduced scale where full size is not needed, so prep is faster.
- A short GPU benchmark kernel (`ml/kaggle/v1_bench`) trains YOLO26 s, m and
  l at 1024 px on about 2,000 images for 3 epochs each. It measures real
  speed, checks DDP and checks the 5 class names. The main run's model is
  picked from its numbers: the largest model that gets about 20 or more full
  epochs in the window. If none does, use a smaller image size.
- Benchmark result (1024 px, T4 x2, per full epoch of about 30,500 images):
  YOLO26-S 11.4 min, YOLO26-M 24.0 min, YOLO26-L 30.4 min. All about 11 to
  12 GB per GPU. Picked **YOLO26-M, batch 16**: about 18 to 20 epochs in the
  window. L would get about 14, with 3 of them spent on warmup.
- Prep problem found and fixed (23:18): merging dHash near-duplicates with
  union-find chained 27,646 images into one group, which broke the split
  (Norway, India and China all in test). Fix: keep the b0 block split and
  drop each val/test image that has a near-duplicate in another split. The main
  kernel re-splits the prepared images in place (`resplit_existing`) and stops
  before training if val or test has under 2,500 images.
- Main run pushed 23:20 with `time=7.75` hours.
- Main run: `ml/kaggle/v1_main`, shared code in `ml/kaggle/v1_train/train_v1.py`.
  After training it scores val, test (once), and b0 vs v1 on the images that
  are in both the old and new test splits, so neither model trained on them.

## Where we start (b0, shipped as v0-mvp)

YOLO11s, 640 px, 50 epochs, one Kaggle T4.

| | val | test |
| --- | --- | --- |
| mAP50 | 0.580 | 0.594 |
| mAP50-95 | 0.304 | 0.315 |
| Pothole AP50 | 0.494 | 0.482 |
| Pothole P / R at conf 0.25 | 0.651 / 0.400 | 0.645 / 0.393 |

Weak spots: pothole recall, Norway (huge images shrunk to 640), India
transverse cracks, no night data. mAP was still rising at epoch 50.
2,118 near-duplicate pairs sit across splits, so these numbers are a bit high.

## The choice: YOLO26-L at 1024 px

| Option | Why / why not |
| --- | --- |
| **YOLO26-L, 1024 px (pick)** | In our pinned Ultralytics 8.4.210, so `service.py` loads it unchanged. Newer than YOLO11, with better small-object training (STAL, ProgLoss) and no NMS. L is about 4x the compute of 11s; 1024 px fixes the Norway shrink and small potholes. |
| YOLO26-X, 1024 px | Maybe +1 to 2 mAP over L, but about 2x slower to train, so far fewer epochs overnight. Likely 4 s or more per image on the laptop CPU. |
| RF-DETR / DEIM (transformers) | Strong on fine-tuning tests, but new package, new inference code, slower to converge. Too risky for one shot with no test run. |
| YOLO11-L | Safe but older than YOLO26 at the same cost. Fallback only if YOLO26 breaks in the smoke test. |

The model size depends on the GPU (see "Compute" below).

## What changes from b0, ranked by expected gain

1. **Image size 640 to 1024.** Biggest single gain for Norway and small potholes.
2. **Bigger model (s to L).**
3. **More training, with a schedule that fits the clock.** Use Ultralytics
   `time=` (hours) so the learning rate schedule finishes inside the window
   instead of being cut off. Start from COCO weights.
4. **Add class 4: Manhole (RDD2022 code D50, 3,581 boxes we threw away).**
   Covers a second PRD issue type for free. Needs a one-line `label_map`
   change in `service.py`.
5. **Fix the split.** Merge dHash near-duplicates into the same group, so the
   new scores are honest. Re-score b0 on the new split for a fair comparison.
6. **Augmentation for night/low light:** stronger HSV value range and some
   brightness drops. Keep mosaic; close mosaic for the last 10% of training.
7. **After training (free):** tune the confidence threshold per class on val,
   and measure test-time augmentation (TTA) on val. Use TTA only if it helps
   and the time per image is still okay.

Not in this run (no time to check they help): extra pothole datasets from
`NEXT_DATA.md`, SAHI tiling, CMIRD classes. They go to v2.

## Compute: the one decision that matters most

| GPU | Model | Est. epochs in 8 h | Notes |
| --- | --- | --- | --- |
| Kaggle 1x T4 (free) | YOLO26-M, 1024 | about 15 to 20 | Too few epochs for L. Session cap 12 h. |
| Kaggle 2x T4 (free) | YOLO26-M, 1024 | about 25 to 30 | DDP. Data loading on 4 CPU cores is still a limit. |
| Rented A100 80 GB | YOLO26-L, 1024 | about 50 to 70 | About $1.5 to 2 per hour, about $15 to 20 total. |
| Rented H100 | YOLO26-L, 1024 (or X) | about 80 to 100 | About $2.5 to 3 per hour, about $25 total. Best result. |

These are rough guesses from the b0 speed and FLOPs. The first epoch's time
tells us the real number, and `time=` adjusts the schedule either way.

## Timeline (IST)

| Time | Step |
| --- | --- |
| 22:45 to 23:30 | Rebuild split (dHash groups, add D50), pre-resize to 1280 max side, cache. Copy to the GPU box. |
| 23:30 to 23:45 | Smoke test: 2% of data, 1 epoch, check loss falls, labels and 5 class names right, checkpoint saves. |
| 23:45 to 07:45 | Main run, `time=8`, `save_period` checkpoints so a crash loses little. |
| 07:45 to 08:45 | Val: threshold sweep, TTA check. Test split once. b0 on the new split. CPU time per image. |
| 08:45 to 09:45 | Weights into `backend/inference/weights/`, update `label_map` and model version, model card, EXPERIMENTS entry, PR. |
| 09:45 to 10:00 | Buffer. If v1 is not better than b0 on the new split, keep v0. |

## Main run settings (draft)

```
model=yolo26l.pt imgsz=1024 time=8 batch=-1 (auto, about 60% GPU memory)
optimizer=auto cos_lr=True close_mosaic=10 patience=0 seed=42
hsv_v=0.5 degrees=0 fliplr=0.5 mosaic=1.0 mixup=0.1
cache=ram workers=<cores> amp=True save_period=5
```

## Risks

- **One shot, no A/B.** We cannot test each change on its own. The smoke test
  only catches crashes, not bad choices. Every change above is a standard,
  well-known gain, which is why it is in.
- **CPU speed.** YOLO26-L at 1024 is about 10x the work of b0. On the i5
  laptop that is about 2 s per image, right on the PRD limit. Fix: export to
  ONNX or OpenVINO, or run at 640 or 800 with some accuracy loss. X would miss
  the limit.
- **PRD pothole target (P 0.90, R 0.85)** will very likely still not be met.
  Published RDD2022 results are well below it. We report the real gap.
- **Night images** still barely in the data; augmentation only helps a little.
