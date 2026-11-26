# Training body shared by every v1 run (b1, seeds, e1-e7). One run = one entry in ml/kaggle/runs.json, which sets
# the environment variables below; build it with
#   python ml/kaggle/build_kernel.py --run <name>
# Input: the output of the infrascan-data-v1 kernel (tars + data_report/), plus the infrascan-cmird-pothole
# dataset when EXTRA=cmird. Scored on val only; test stays untouched unless EVAL_TEST=1 (final model only).
# Environment variables also let the same file run as a tiny CPU smoke test locally
# (WORK_DIR, TMP_DIR, DATA_DIR, EXTRA_DIR, DEVICE=cpu).
import csv
import json
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import time
from pathlib import Path

RUN = os.environ.get("RUN", "b1_yolo11s_640")
MODEL = os.environ.get("MODEL", "yolo11s.pt")
IMGSZ = int(os.environ.get("IMGSZ", 640))
EPOCHS = int(os.environ.get("EPOCHS", 50))
PATIENCE = int(os.environ.get("PATIENCE", 15))
BATCH = int(os.environ.get("BATCH", 32))
DEVICE = os.environ.get("DEVICE", "0")
DEVICE = int(DEVICE) if DEVICE.isdigit() else DEVICE
SEED = int(os.environ.get("SEED", 42))  # 42 for the baseline; other seeds for run-to-run variance
CLASSES = os.environ.get("CLASSES", "rdd4")  # rdd4 = 4 damage classes; "pothole" = potholes only (class 0)
ULTRA_VERSION = "8.4.210"
WORK = Path(os.environ.get("WORK_DIR", "/kaggle/working"))
TMP = Path(os.environ.get("TMP_DIR", "/tmp/infrascan"))
EVAL_TEST = os.environ.get("EVAL_TEST", "0") == "1"  # keep 0: test is scored once, for the final model
EXTRA = os.environ.get("EXTRA", "")  # "" = RDD2022 only; "cmird" = add CMIRD Indian pothole frames to train
# CMIRD videos kept out of training, so the model can be scored on motorbike/monsoon footage it has never seen
CMIRD_HOLDOUT = {v for v in os.environ.get("CMIRD_HOLDOUT", "").split(",") if v}
# Speed settings (do not change what the model learns). Kaggle's GPU machine has 2 T4s and 4 CPUs; Ultralytics
# gives each visible GPU cpu_count // n_gpus loader workers, so with both GPUs visible b0-b1 ran on 2 workers
# while training on GPU 0 only. Hiding the unused GPU lets all 4 CPUs load data.
WORKERS = int(os.environ.get("WORKERS", 4))
CACHE = os.environ.get("CACHE", "none")  # "ram" / "disk" / "none". Not "ram" by default: e4 (16 Nov) cached
# ~13 GB of images, then 4 forked loader workers grew past Kaggle's RAM and the run was "Killed" after epoch 4.
DETERMINISTIC = os.environ.get("DETERMINISTIC", "1") == "1"
if isinstance(DEVICE, int) and "CUDA_VISIBLE_DEVICES" not in os.environ:
    os.environ["CUDA_VISIBLE_DEVICES"] = str(DEVICE)  # must happen before torch is imported
    DEVICE = 0

t0 = time.time()
WORK.mkdir(parents=True, exist_ok=True)


def log(msg):
    print(f"[{time.time() - t0:7.0f}s] {msg}", flush=True)


# 1. pinned Ultralytics
try:
    import ultralytics
    have = ultralytics.__version__
except ImportError:
    have = None
if have != ULTRA_VERSION:
    subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", f"ultralytics=={ULTRA_VERSION}"])
import torch  # noqa: E402
import ultralytics  # noqa: E402
from ultralytics import YOLO  # noqa: E402

import evaluate  # noqa: E402
import prepare_rdd2022  # noqa: E402

env = {"ultralytics": ultralytics.__version__, "torch": torch.__version__, "python": platform.python_version(),
       "cuda": torch.cuda.is_available(),
       "gpu": torch.cuda.get_device_name(0) if torch.cuda.is_available() else None,
       "cpus": os.cpu_count(), "visible_gpus": torch.cuda.device_count(),
       "loader_workers": min(os.cpu_count() // max(torch.cuda.device_count(), 1), WORKERS)}
log(f"env {env}")

# 2. find the data_v1 output
data_dir = os.environ.get("DATA_DIR")
if not data_dir:
    hits = sorted(Path("/kaggle/input").rglob("rdd2022_v1_train.tar"))
    if not hits:
        raise SystemExit("rdd2022_v1_train.tar not found under /kaggle/input (attach the infrascan-data-v1 output)")
    data_dir = str(hits[0].parent)
data_dir = Path(data_dir)
src_stats = json.loads((data_dir / "data_report" / "stats.json").read_text())
log(f"data {data_dir}: class_set {src_stats.get('class_set')}, split_rule {src_stats.get('split_rule')}, "
    f"confirmed dups crossing splits {src_stats['confirmed_duplicate_pairs_across_splits']['total']}, "
    f"look-alike pairs crossing {src_stats['lookalike_pairs_across_splits']['total']}")
if src_stats["confirmed_duplicate_pairs_across_splits"]["total"] != 0:
    raise SystemExit("data_v1 split leaks confirmed duplicates; refusing to train")

# 3. extract a fresh local copy, then switch to the class set for this run
data_root = TMP / "rdd2022_v1"
if data_root.exists():
    shutil.rmtree(data_root)
data_root.mkdir(parents=True)
for split in ("train", "val", "test") if EVAL_TEST else ("train", "val"):
    with tarfile.open(data_dir / f"rdd2022_v1_{split}.tar") as tar:
        tar.extractall(data_root)

# 3b. add the CMIRD Indian pothole frames to TRAIN only (built by ml/data/prepare_cmird.py; already checked
# against every RDD2022 val/test image). CMIRD has one "crack" class but its crack boxes are not written, so a
# CMIRD frame that shows cracks would teach the crack classes "no crack here": such frames are used only when the
# class set has no crack classes ("pothole").
extra_added = None
if EXTRA == "cmird":
    extra_hits = sorted(Path(os.environ.get("EXTRA_DIR", "/kaggle/input")).rglob("cmird_manifest.csv"))
    if not extra_hits:
        raise SystemExit("cmird_manifest.csv not found (attach the infrascan-cmird-pothole dataset)")
    extra_dir = extra_hits[0].parent
    keep_crack = CLASSES == "pothole"
    extra_added = {"frames": 0, "boxes": 0, "skipped_crack_frames": 0, "skipped_holdout_frames": 0}
    with open(extra_hits[0], newline="") as f:
        for r in csv.DictReader(f):
            if r["video"] in CMIRD_HOLDOUT:
                extra_added["skipped_holdout_frames"] += 1
                continue
            if int(r["n_crack"]) > 0 and not keep_crack:
                extra_added["skipped_crack_frames"] += 1
                continue
            shutil.copy(extra_dir / "images" / "train" / f"{r['stem']}.jpg", data_root / "images" / "train")
            shutil.copy(extra_dir / "labels" / "train" / f"{r['stem']}.txt", data_root / "labels" / "train")
            extra_added["frames"] += 1
            extra_added["boxes"] += int(r["n_pothole"])
    log(f"CMIRD added to train: {extra_added} (keep_crack={keep_crack})")
    if extra_added["frames"] == 0:
        raise SystemExit("no CMIRD frames added")
    extra_added = {"source": "infrascan-cmird-pothole (train only)", **extra_added, "keep_crack": keep_crack,
                   "holdout_videos": sorted(CMIRD_HOLDOUT)}
elif EXTRA:
    raise SystemExit(f"unknown EXTRA={EXTRA!r}")

data_yaml, removed = prepare_rdd2022.use_class_set(data_root, CLASSES)
data_yaml = str(data_yaml)
manifest = str(data_dir / "data_report" / "manifest.csv")
shutil.copytree(data_dir / "data_report", WORK / "data_report", dirs_exist_ok=True)
counts = {s: len(list((data_root / "images" / s).glob("*.jpg"))) for s in ("train", "val", "test")}
log(f"data ready: {counts}, classes {CLASSES}, removed {removed} box lines outside the class set")

# 4. train
YOLO(MODEL).train(data=data_yaml, epochs=EPOCHS, patience=PATIENCE, imgsz=IMGSZ, batch=BATCH, device=DEVICE,
                  seed=SEED, deterministic=DETERMINISTIC, workers=WORKERS, project=str(WORK / "runs"), name=RUN,
                  exist_ok=True, plots=True, amp=True, cache=False if CACHE == "none" else CACHE)
log("training done")

# 5. evaluate best weights on val (test only if EVAL_TEST=1)
bm = YOLO(str(WORK / "runs" / RUN / "weights" / "best.pt"))
evaluate.full_report(bm, data_root, data_yaml, manifest, "val", IMGSZ, WORK / "eval_val.json", device=DEVICE)
log("val eval done")
if EVAL_TEST:
    evaluate.full_report(bm, data_root, data_yaml, manifest, "test", IMGSZ, WORK / "eval_test.json", device=DEVICE)
    log("test eval done")

env["run"] = RUN
env["config"] = {"model": MODEL, "imgsz": IMGSZ, "epochs": EPOCHS, "patience": PATIENCE, "batch": BATCH,
                 "seed": SEED, "classes": CLASSES, "cache": CACHE, "deterministic": DETERMINISTIC,
                 "workers": WORKERS, "countries": src_stats.get("countries"), "data": "infrascan-data-v1",
                 "split_rule": src_stats.get("split_rule"), "images": counts, "extra": extra_added}
env["minutes"] = round((time.time() - t0) / 60, 1)
(WORK / "env.json").write_text(json.dumps(env, indent=1))
log("all done")
