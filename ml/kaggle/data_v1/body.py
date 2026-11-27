# data_v1: prepare RDD2022 once, on CPU, for every v1 training run.
# - Leak-free split (near-duplicates merged into one split unit).
# - rdd5 labels (D50 manhole cover = class 4). An rdd4 label set is the same
#   files with class-4 lines removed (same split, ids 0-3 unchanged); training
#   kernels make it with prepare_rdd2022.use_class_set(data_root, "rdd4").
# - Output: one uncompressed tar per split (JPEGs are already compressed;
#   a few big files are safer and faster than ~38k small output files) plus
#   data_report/ (stats.json, manifest.csv). Save this notebook's output as a
#   private Kaggle dataset and attach it to the training kernels.
# Environment variables let the same file run locally as a smoke test.
import json
import os
import subprocess
import sys
import tarfile
import time
from pathlib import Path

WORK = Path(os.environ.get("WORK_DIR", "/kaggle/working"))
TMP = Path(os.environ.get("TMP_DIR", "/tmp/infrascan"))
WORKERS = int(os.environ.get("WORKERS", os.cpu_count() or 4))
CLASSES = os.environ.get("CLASSES", "rdd5")
DUP_DIST = os.environ.get("DUP_DIST", "4")
BALANCE_TOL = float(os.environ.get("BALANCE_TOL", "0.03"))  # val/test share must be 10% +- this (raise only for toy tests)
DUP_MAD = os.environ.get("DUP_MAD", "5")  # thumbnail confirmation; see prepare_rdd2022.py
COUNTRIES = os.environ.get("COUNTRIES", "India,United_States")  # PRD pilots: Bengaluru, California; "all" = every country

t0 = time.time()
WORK.mkdir(parents=True, exist_ok=True)


def log(msg):
    print(f"[{time.time() - t0:7.0f}s] {msg}", flush=True)


if __name__ == "__main__":
    src = os.environ.get("DATA_SRC")
    if not src:
        hits = sorted(Path("/kaggle/input").rglob("RDD2022_all_countries"))
        if not hits:
            raise SystemExit("RDD2022_all_countries not found under /kaggle/input")
        src = str(hits[0])
    log(f"data source {src}, workers {WORKERS}, classes {CLASSES}, dup_dist {DUP_DIST}, dup_mad {DUP_MAD}, countries {COUNTRIES}")

    data_root = TMP / "rdd2022_v1"
    report_dir = WORK / "data_report"
    subprocess.check_call([sys.executable, str(Path(sys.path[0]) / "prepare_rdd2022.py"), "--src", src,
                           "--out", str(data_root), "--stats-dir", str(report_dir), "--workers", str(WORKERS),
                           "--classes", CLASSES, "--dup-dist", DUP_DIST, "--dup-mad", DUP_MAD, "--countries", COUNTRIES])
    log("prep done")

    st = json.loads((report_dir / "stats.json").read_text())
    cross = st["confirmed_duplicate_pairs_across_splits"]["total"]
    log(f"confirmed duplicates {st['confirmed_duplicate_pairs_total']}, crossing splits {cross}; "
        f"look-alike pairs crossing splits {st['lookalike_pairs_across_splits']['total']}; split units: {st['split_units']}")
    problems = [f"{cross} confirmed duplicate pairs cross splits"] if cross else []
    for country, b in st["split_balance"].items():
        for part in ("val", "test"):
            share = b[f"{part}_share"]
            if abs(share - 0.10) > BALANCE_TOL:
                problems.append(f"{country} {part} share {share:.3f} is outside 0.10 +- {BALANCE_TOL}")
            if b["pothole_images"][part] == 0:
                problems.append(f"{country} has no pothole images in {part}")
    log("balance: " + json.dumps(st["split_balance"]))
    if problems:
        raise SystemExit("split check failed: " + "; ".join(problems))

    for split in ("train", "val", "test"):
        with tarfile.open(WORK / f"rdd2022_v1_{split}.tar", "w") as tar:
            tar.add(data_root / "images" / split, arcname=f"images/{split}")
            tar.add(data_root / "labels" / split, arcname=f"labels/{split}")
        log(f"wrote rdd2022_v1_{split}.tar")
    (WORK / "data.yaml").write_text((data_root / "data.yaml").read_text().replace(str(data_root), "."))
    log("all done")
