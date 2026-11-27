# ml/ — road damage detection model

Training data prep, Kaggle training jobs, evaluation and results for the detector used by
`backend/inference/`. Training runs on Kaggle GPUs; everything here also runs on a laptop CPU
for checks and analysis.

## Layout

```
ml/
  README.md          this file: layout, how to run things, model facts, rules
  EXPERIMENTS.md     one entry per run: config, numbers, what we learned
  MODEL_CARD.md      what the shipped model is, how good it is, limits
  NEXT_DATA.md       candidate extra datasets, with what was checked
  requirements.txt   local tooling (pinned Ultralytics, pytest, kaggle CLI)

  data/              code that builds datasets (no data is stored in the repo)
    prepare_rdd2022.py   RDD2022 -> YOLO folders, leak-free split, class sets (rdd4, rdd5, pothole)
    prepare_cmird.py     CMIRD Indian pothole frames -> train-only add-on

  eval/              scoring and analysis of a trained model
    boxes.py             IoU, the one box-matching rule, YOLO label reading (shared by the files below)
    evaluate.py          full report on a split (per class, per country, P/R at several cut-offs) + saved predictions
    analyze_preds.py     from saved predictions: confidence intervals, run-vs-run, size slices, calibration
    error_sheets.py      picture sheets of missed potholes and false alarms
    ensemble.py          merge several models / test-time augmentation (weighted box fusion), photo-level counts
    score_holdout.py     score a model on the CMIRD videos a run kept out of training (e7)

  kaggle/            everything that runs on Kaggle
    build_kernel.py      packs a body + the helper modules it uses into one Kaggle script
    runs.json            list of training runs (settings, Kaggle kernel, data sources, status)
    train/body.py        the one training body every v1 run uses
    data_v1/             CPU job: builds the leak-free India+US dataset (input to every v1 run)
    dup_analysis/        CPU job: near-duplicate analysis behind the split fix; its thumbs.npz is also the
                         input prepare_cmird.py uses to keep extra data out of val/test
    _build/              generated kernels (git-ignored)

  reports/           results copied back from Kaggle (small files only; no weights, no datasets)
    data_v1/             stats.json + manifest.csv of the dataset every v1 run uses
    <run>/               env.json, eval_val.json (eval_test.json once), args.yaml, results.csv/png, val curves

  tests/             pytest: python -m pytest ml/tests
```

Not in the repo: datasets, weights (`backend/inference/weights/best.pt` is the one shipped model),
full prediction dumps, and personal notes.

## Common tasks

Set up (once): `pip install -r ml/requirements.txt` and a Kaggle API token.

Run a training job:
1. Add or pick an entry in `ml/kaggle/runs.json` (one change from b1 per run).
2. `python ml/kaggle/build_kernel.py --run <name>`
3. `kaggle kernels push -p ml/kaggle/_build/<name>` (uses GPU quota)
4. When it finishes: `kaggle kernels output <kernel id> -p <somewhere outside the repo>`, copy the small
   files into `ml/reports/<name>/`, and add an entry to `EXPERIMENTS.md`.

Rebuild the dataset (CPU job, no GPU quota):
`python ml/kaggle/build_kernel.py ml/kaggle/data_v1/body.py ml/kaggle/data_v1/run.py`, then
`kaggle kernels push -p ml/kaggle/data_v1`. The split is fixed by seed, so a rebuild gives the same split.

Training speed settings (env in `runs.json`; they change speed, not what the model learns):
`WORKERS` (4), `CACHE` (`none` default; `ram` ran out of memory on Kaggle with 4 workers, see EXPERIMENTS
sec. 7), `DETERMINISTIC` (`1` default). The body hides the unused second Kaggle GPU so all 4 CPUs load data
(b0-b1 ran on 2 loader workers). `env.json` records `loader_workers` and the settings.

Compare two runs (confidence intervals that respect correlated video frames):
`python ml/eval/analyze_preds.py <a>/eval_val_preds.json --compare <b>/eval_val_preds.json --manifest ml/reports/data_v1/manifest.csv`

Score on held-out CMIRD videos (footage unlike RDD2022; run once per model, then compare):
`python ml/eval/score_holdout.py <best.pt> --name e7 --cmird <prepared CMIRD dir> --out <dir>`, then
`python ml/eval/ensemble.py compare --base <dir>/e4_preds.json --manifest <dir>/holdout_manifest.csv e7=<dir>/e7_preds.json`

## Model facts

Classes (fixed order, same as the training labels and `label_map` in `backend/inference/service.py`):

| id | code | label |
| --- | --- | --- |
| 0 | D00 | Longitudinal crack |
| 1 | D10 | Transverse crack |
| 2 | D20 | Alligator crack |
| 3 | D40 | Pothole |

- Only "Pothole" matches one of the six PRD issue types. Manholes, water clogging, broken footpaths, speed
  breakers and encroachments have no training data in RDD2022 (see `NEXT_DATA.md`).
- Data: RDD2022 from the Kaggle mirror `gurunathan88/rdd2022` (mirror says MIT; the official licence is
  CC BY-SA 4.0, which we follow). 38,385 labeled images in 7 countries; the official test folders have no
  labels, so all scores use our own 80/10/10 split. Since data_v1 (14 Nov 2026) we use India + United States
  only (the PRD pilots, Bengaluru and California), split leak-free (`prepare_rdd2022.py`).
- Night is essentially missing: only 111 of 38,385 images have mean brightness below 60.
- PRD targets: pothole precision at least 90% and recall at least 85%; under 2 s per frame and under 5 s end
  to end per image. Published RDD2022 results are well below the accuracy target; we report the real numbers.
- Severity: RDD2022 has no severity labels. The app uses a rule on box area plus class
  (`severity_heuristic` in `service.py`), labelled "heuristic, not validated".

## Rules we keep

- Val for every decision; test is scored once, for the final model.
- One change per run, compared with b1 on the same val images. Differences under about 0.05 pothole AP50
  are within run-to-run noise (3 seeds of b1).
- Class ids 0-3 never change (`label_map` in `backend/inference/service.py` depends on them).
- Extra data goes into train only and is checked for near-duplicates against val/test first.
