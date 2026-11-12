# Run b0: baseline. YOLO11s, COCO pretrained, imgsz 640, 50 epochs, patience 15,
# default augmentation, 4 RDD2022 classes. Feeds Milestone A (v0).
# Environment variables let the same file run as a tiny CPU smoke test locally.
import json
import os
import platform
import subprocess
import sys
import time
from pathlib import Path

RUN = os.environ.get("RUN", "b0_yolo11s_640")
MODEL = os.environ.get("MODEL", "yolo11s.pt")
IMGSZ = int(os.environ.get("IMGSZ", 640))
EPOCHS = int(os.environ.get("EPOCHS", 50))
PATIENCE = int(os.environ.get("PATIENCE", 15))
BATCH = int(os.environ.get("BATCH", 32))
DEVICE = os.environ.get("DEVICE", "0")
DEVICE = int(DEVICE) if DEVICE.isdigit() else DEVICE
WORKERS = int(os.environ.get("WORKERS", 4))
ULTRA_VERSION = "8.4.210"
WORK = Path(os.environ.get("WORK_DIR", "/kaggle/working"))
TMP = Path(os.environ.get("TMP_DIR", "/tmp/infrascan"))
EVAL_TEST = os.environ.get("EVAL_TEST", "1") == "1"  # v0 only: test split scored once at the end

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

env = {"ultralytics": ultralytics.__version__, "torch": torch.__version__, "python": platform.python_version(),
       "cuda": torch.cuda.is_available(),
       "gpu": torch.cuda.get_device_name(0) if torch.cuda.is_available() else None}
log(f"env {env}")

# 2. find RDD2022 source
src = os.environ.get("DATA_SRC")
if not src:
    hits = sorted(Path("/kaggle/input").rglob("RDD2022_all_countries"))
    if not hits:
        raise SystemExit("RDD2022_all_countries not found under /kaggle/input")
    src = str(hits[0])
log(f"data source {src}")

# 3. prepare data (convert, check, split)
data_root = TMP / "rdd2022_yolo"
report_dir = WORK / "data_report"
if not (data_root / "data.yaml").exists():
    subprocess.check_call([sys.executable, str(Path(sys.path[0]) / "prepare_rdd2022.py"), "--src", src,
                           "--out", str(data_root), "--stats-dir", str(report_dir), "--workers", str(WORKERS)])
log("data ready")
data_yaml = str(data_root / "data.yaml")

# 4. train
model = YOLO(MODEL)
model.train(data=data_yaml, epochs=EPOCHS, patience=PATIENCE, imgsz=IMGSZ, batch=BATCH, device=DEVICE,
            seed=42, deterministic=True, workers=WORKERS, project=str(WORK / "runs"), name=RUN,
            exist_ok=True, plots=True, amp=True, cache=False)
log("training done")

# 5. evaluate best weights
import evaluate  # noqa: E402

best = WORK / "runs" / RUN / "weights" / "best.pt"
bm = YOLO(str(best))
manifest = str(report_dir / "manifest.csv")
evaluate.full_report(bm, data_root, data_yaml, manifest, "val", IMGSZ, WORK / "eval_val.json", device=DEVICE)
log("val eval done")
if EVAL_TEST:
    evaluate.full_report(bm, data_root, data_yaml, manifest, "test", IMGSZ, WORK / "eval_test.json", device=DEVICE)
    log("test eval done")

env["run"] = RUN
env["config"] = {"model": MODEL, "imgsz": IMGSZ, "epochs": EPOCHS, "patience": PATIENCE, "batch": BATCH,
                 "seed": 42, "data_source": src}
env["minutes"] = round((time.time() - t0) / 60, 1)
(WORK / "env.json").write_text(json.dumps(env, indent=1))
log("all done")
