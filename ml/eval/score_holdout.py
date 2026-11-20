"""Score a model on the CMIRD videos a run held out of training (e.g. e7_yolo26s_cmird_holdout).

The held-out video list is read from the run's CMIRD_HOLDOUT setting in ml/kaggle/runs.json (the same value the
training body used), so training and scoring cannot disagree. Writes, in --out:
- <name>_preds.json: saved predictions (evaluate.dump_entry format), read by ensemble.py / analyze_preds.py
- holdout_manifest.csv: stem,unit with unit = video, so confidence intervals treat one video as one unit

Usage (CPU is fine: 119 frames take about a minute):
  python ml/eval/score_holdout.py <best.pt> --name e7 --run e7_yolo26s_cmird_holdout \
      --cmird <prepared CMIRD dir> --out <dir>
then compare two models on the same frames:
  python ml/eval/ensemble.py compare --base <dir>/e4_preds.json --manifest <dir>/holdout_manifest.csv \
      e7=<dir>/e7_preds.json

<prepared CMIRD dir> is the output of ml/data/prepare_cmird.py (Kaggle dataset infrascan-cmird-pothole):
cmird_manifest.csv, images/train/<stem>.jpg, labels/train/<stem>.txt.
"""

import argparse
import csv
import json
from pathlib import Path

from boxes import read_yolo_labels
from evaluate import collect_predictions, dump_entry

RUNS_JSON = Path(__file__).resolve().parents[1] / "kaggle" / "runs.json"


def holdout_videos(run, runs_json=RUNS_JSON):
    """The CMIRD_HOLDOUT videos of a run in runs.json, as a sorted list."""
    runs = json.loads(Path(runs_json).read_text(encoding="utf-8"))
    runs = runs.get("runs", runs)
    value = runs[run].get("env", {}).get("CMIRD_HOLDOUT", "")
    videos = sorted(v for v in value.split(",") if v)
    if not videos:
        raise SystemExit(f"run {run} has no CMIRD_HOLDOUT setting in {runs_json}")
    return videos


def holdout_rows(manifest_csv, videos):
    """Rows of cmird_manifest.csv whose video is held out, in file order."""
    with open(manifest_csv, newline="") as f:
        rows = [r for r in csv.DictReader(f) if r["video"] in set(videos)]
    missing = set(videos) - {r["video"] for r in rows}
    if missing:
        raise SystemExit(f"videos not in {manifest_csv}: {sorted(missing)}")
    return rows


def write_unit_manifest(rows, path):
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["stem", "unit"])
        w.writerows([r["stem"], r["video"]] for r in rows)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("weights")
    ap.add_argument("--name", required=True, help="prefix of the predictions file, e.g. e7")
    ap.add_argument("--run", default="e7_yolo26s_cmird_holdout", help="runs.json entry with CMIRD_HOLDOUT")
    ap.add_argument("--cmird", required=True, help="prepared CMIRD dir (prepare_cmird.py output)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--imgsz", type=int, default=640)
    ap.add_argument("--device", default="cpu")
    a = ap.parse_args()

    src, out = Path(a.cmird), Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    rows = holdout_rows(src / "cmird_manifest.csv", holdout_videos(a.run))
    write_unit_manifest(rows, out / "holdout_manifest.csv")

    from ultralytics import YOLO
    paths = [str(src / "images" / "train" / f"{r['stem']}.jpg") for r in rows]
    # batch=1: one frame at a time reproduces the recorded e7 numbers exactly; batches of 32 shift scores by
    # about 0.001 (CPU rounding), which moves no headline number but changes the saved file.
    preds = collect_predictions(YOLO(a.weights), paths, a.imgsz, batch=1, device=a.device)
    dump = {}
    for r, p in zip(rows, paths):
        e = preds[p]
        gt = read_yolo_labels(src / "labels" / "train" / f"{r['stem']}.txt", e["w"], e["h"])
        dump[r["stem"]] = dump_entry(e["det"], e["w"], e["h"], gt)
    (out / f"{a.name}_preds.json").write_text(json.dumps(dump))
    n_videos = len({r["video"] for r in rows})
    print(f"{a.name}: {len(dump)} frames from {n_videos} videos, {sum(len(v['gt']) for v in dump.values())} "
          f"labelled boxes -> {out / (a.name + '_preds.json')}")


if __name__ == "__main__":
    main()
