# v1 training on Kaggle GPU T4 x2 (DDP on both GPUs). One shared body for two modes:
#   MODE=bench: tiny prep of the raw data, 3 short epochs per model, records
#               speed so we can pick the model and batch for the night.
#   MODE=main:  uses the v1 prep kernel output (5 classes, dup-aware split,
#               long side 1024), trains for HOURS with Ultralytics time=, then
#               scores val, test once, and b0 vs v1 on images neither trained on.
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

MODE = os.environ.get("MODE", "main")
RUN = os.environ.get("RUN", "v1")
MODELS = os.environ.get("MODELS", "yolo26m.pt:16").split(",")  # model:batch (total over both GPUs)
IMGSZ = int(os.environ.get("IMGSZ", 1024))
HOURS = float(os.environ.get("HOURS", 8))
WORKERS = int(os.environ.get("WORKERS", 4))
ULTRA_VERSION = "8.4.210"
WORK = Path("/kaggle/working")
TMP = Path("/kaggle/temp")

t0 = time.time()
WORK.mkdir(parents=True, exist_ok=True)
TMP.mkdir(parents=True, exist_ok=True)


def log(msg):
    print(f"[{time.time() - t0:7.0f}s] {msg}", flush=True)


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

N_GPU = torch.cuda.device_count()
DEVICE = list(range(N_GPU)) if N_GPU > 1 else 0
env = {"ultralytics": ultralytics.__version__, "torch": torch.__version__, "python": platform.python_version(),
       "gpus": [torch.cuda.get_device_name(i) for i in range(N_GPU)], "cpus": os.cpu_count(), "mode": MODE}
log(f"env {env}")


def fix_yaml(root):
    y = root / "data.yaml"
    lines = y.read_text().split("\n")
    lines[0] = f"path: {root}"
    y.write_text("\n".join(lines))
    return str(y)


# ---------- data ----------
if MODE == "bench":
    hits = sorted(Path("/kaggle/input").rglob("RDD2022_all_countries"))
    data_root = TMP / "bench_data"
    subprocess.check_call([sys.executable, str(Path(sys.path[0]) / "prepare_rdd2022.py"), "--src", str(hits[0]),
                           "--out", str(data_root), "--workers", str(os.cpu_count()), "--manhole", "--dup-groups",
                           "--max-side", "1024", "--limit", "300"])
    report_dir = data_root
else:
    tars = sorted(Path("/kaggle/input").rglob("rdd2022_v1.tar"))
    if not tars:
        raise SystemExit("rdd2022_v1.tar not found; attach the infrascan-v1-prep kernel output")
    with tarfile.open(tars[0]) as tf:
        tf.extractall(TMP)
    data_root = TMP / "rdd2022_v1"
    shutil.copytree(tars[0].parent / "data_report", WORK / "data_report_prep", dirs_exist_ok=True)
    # The prep kernel's union-find dup grouping chained ~27k images into one group,
    # so its split is unusable. Re-split here: b0 block split + drop eval images
    # that have a near-duplicate in another split.
    import prepare_rdd2022  # noqa: E402
    report_dir = WORK / "data_report"
    report_dir.mkdir(exist_ok=True)
    rs = prepare_rdd2022.resplit_existing(data_root, WORK / "data_report_prep" / "manifest.csv",
                                          report_dir / "manifest.csv", workers=os.cpu_count())
    (report_dir / "resplit.json").write_text(json.dumps(rs, indent=1))
    log(f"resplit {json.dumps(rs)}")
    if rs["per_split"].get("val", 0) < 2500 or rs["per_split"].get("test", 0) < 2500:
        raise SystemExit("re-split looks wrong (val or test under 2,500 images); stopping before training")
data_yaml = fix_yaml(data_root)
n_train = len(list((data_root / "images" / "train").glob("*.jpg")))
log(f"data ready: {data_root}, train images {n_train}")

# ---------- bench ----------
if MODE == "bench":
    rows = []
    for spec in MODELS:
        name, batch = spec.split(":")
        run = f"bench_{Path(name).stem}"
        row = {"model": name, "batch": int(batch), "imgsz": IMGSZ}
        try:
            ts = time.time()
            YOLO(name).train(data=data_yaml, epochs=3, imgsz=IMGSZ, batch=int(batch), device=DEVICE, workers=WORKERS,
                             project=str(WORK / "runs"), name=run, exist_ok=True, plots=False, amp=True, val=False,
                             seed=42)
            row["total_s"] = round(time.time() - ts, 1)
            with open(WORK / "runs" / run / "results.csv") as f:
                t = [float(r["time"]) for r in csv.DictReader(f)]
            per_epoch = (t[-1] - t[0]) / (len(t) - 1)  # skip the first epoch (start-up)
            row["s_per_epoch_bench"] = round(per_epoch, 1)
            row["img_per_s"] = round(n_train / per_epoch, 1)
            row["full_epoch_min_est"] = round(30500 / row["img_per_s"] / 60, 1)
            row["epochs_in_8h_est"] = round(8 * 60 / row["full_epoch_min_est"], 1)
        except Exception as e:  # noqa: BLE001
            row["error"] = repr(e)[:500]
        rows.append(row)
        log(f"bench {row}")
        (WORK / "bench.json").write_text(json.dumps({"env": env, "n_train": n_train, "rows": rows}, indent=1))
        torch.cuda.empty_cache()
    log("bench done")
    raise SystemExit(0)

# ---------- main train ----------
name, batch = MODELS[0].split(":")
model = YOLO(name)
model.train(data=data_yaml, epochs=100, time=HOURS, imgsz=IMGSZ, batch=int(batch), device=DEVICE, workers=WORKERS,
            seed=42, project=str(WORK / "runs"), name=RUN, exist_ok=True, plots=True, amp=True, cache=False,
            patience=0, close_mosaic=int(os.environ.get("CLOSE_MOSAIC", 3)), hsv_v=0.5)
log("training done")

# ---------- evaluate ----------
import evaluate  # noqa: E402

best = WORK / "runs" / RUN / "weights" / "best.pt"
bm = YOLO(str(best))
manifest = str(report_dir / "manifest.csv")
evaluate.full_report(bm, data_root, data_yaml, manifest, "val", IMGSZ, WORK / "eval_val.json", device=0)
log("val eval done")
evaluate.full_report(bm, data_root, data_yaml, manifest, "test", IMGSZ, WORK / "eval_test.json", device=0)
log("test eval done")

# ---------- b0 vs v1 on images in BOTH the old and new test splits (neither model trained on them) ----------
try:
    b0_w = [p for p in Path("/kaggle/input").rglob("best.pt") if "b0" in str(p)]
    b0_man = [p for p in Path("/kaggle/input").rglob("manifest.csv") if "b0" in str(p)]
    old_test = {r["stem"] for r in csv.DictReader(open(b0_man[0])) if r["split"] == "test"}
    new_test = {r["stem"] for r in csv.DictReader(open(manifest)) if r["split"] == "test"}
    both = sorted(old_test & new_test)
    cmp = TMP / "cmp4"
    (cmp / "images" / "test").mkdir(parents=True, exist_ok=True)
    (cmp / "labels" / "test").mkdir(parents=True, exist_ok=True)
    for s in both:
        dst = cmp / "images" / "test" / f"{s}.jpg"
        if not dst.exists():
            os.symlink(data_root / "images" / "test" / f"{s}.jpg", dst)
        lines = (data_root / "labels" / "test" / f"{s}.txt").read_text().split("\n")
        keep = [ln for ln in lines if ln and not ln.startswith("4 ")]
        (cmp / "labels" / "test" / f"{s}.txt").write_text("\n".join(keep) + ("\n" if keep else ""))
    names4 = ["Longitudinal crack", "Transverse crack", "Alligator crack", "Pothole"]
    (cmp / "data.yaml").write_text(f"path: {cmp}\ntrain: images/test\nval: images/test\nnames:\n" +
                                   "".join(f"  {i}: {n}\n" for i, n in enumerate(names4)))
    out = {"images": len(both), "note": "classes 0-3 only; images in old b0 test AND new v1 test"}
    for tag, w, sz in (("b0", b0_w[0], 640), ("v1", best, IMGSZ)):
        m = YOLO(str(w)).val(data=str(cmp / "data.yaml"), split="val", imgsz=sz, batch=16, device=0, plots=False,
                             verbose=False, project=str(WORK / "runs"), name=f"cmp_{tag}", exist_ok=True)
        per = {names4[int(c)]: round(float(m.box.class_result(i)[2]), 4)
               for i, c in enumerate(m.box.ap_class_index) if int(c) < 4}
        out[tag] = {"mAP50_cls0to3": round(sum(per.values()) / len(per), 4), "AP50": per}
    (WORK / "compare_b0_v1.json").write_text(json.dumps(out, indent=1))
    log(f"compare {out}")
except Exception as e:  # noqa: BLE001
    log(f"compare failed: {e!r}")

env.update({"run": RUN, "model": name, "batch": int(batch), "imgsz": IMGSZ, "hours": HOURS,
            "minutes": round((time.time() - t0) / 60, 1)})
(WORK / "env.json").write_text(json.dumps(env, indent=1))
log("all done")
