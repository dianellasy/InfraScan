# v1 data prep (CPU kernel). Builds the v1 dataset once so GPU runs do not spend
# GPU time on it: 5 classes (adds D50 Manhole cover), dHash duplicate-aware
# split groups, long side capped at 1024. Output: one tar plus stats/manifest.
import os
import subprocess
import sys
import tarfile
import time
from pathlib import Path

t0 = time.time()
WORK = Path("/kaggle/working")
OUT = Path("/kaggle/temp/rdd2022_v1")


def log(msg):
    print(f"[{time.time() - t0:7.0f}s] {msg}", flush=True)


hits = sorted(Path("/kaggle/input").rglob("RDD2022_all_countries"))
if not hits:
    raise SystemExit("RDD2022_all_countries not found under /kaggle/input")
src = str(hits[0])
log(f"data source {src}, cpus {os.cpu_count()}")
subprocess.check_call([sys.executable, str(Path(sys.path[0]) / "prepare_rdd2022.py"), "--src", src, "--out", str(OUT),
                       "--stats-dir", str(WORK / "data_report"), "--workers", str(os.cpu_count()),
                       "--manhole", "--dup-groups", "--max-side", "1024"])
log("prep done, writing tar")
with tarfile.open(WORK / "rdd2022_v1.tar", "w") as tf:
    tf.add(OUT, arcname="rdd2022_v1")
log(f"tar {(WORK / 'rdd2022_v1.tar').stat().st_size / 1e9:.2f} GB, all done")
