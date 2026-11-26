# dup_analysis: save a 64x64 grayscale thumbnail and the exact b0/v1 dHash of every
# labeled RDD2022 training image, so near-duplicate rules can be tuned offline
# (seconds per try) instead of one 45-minute Kaggle prep run per try.
# CPU only. Output: thumbs.npz = stems, dhash (uint64), thumbs (N, 64, 64 uint8).
# The hash comes from prepare_rdd2022.dhash on the raw opened image, the same call
# inspect_one makes, so pairs found offline match the ones the prep script finds.
import os
import time
from multiprocessing import Pool
from pathlib import Path

import numpy as np
from PIL import Image

import prepare_rdd2022 as prep

WORK = Path(os.environ.get("WORK_DIR", "/kaggle/working"))
WORKERS = int(os.environ.get("WORKERS", os.cpu_count() or 4))
t0 = time.time()


def log(msg):
    print(f"[{time.time() - t0:7.0f}s] {msg}", flush=True)


def one(img_path):
    with Image.open(img_path) as im:
        h = prep.dhash(im)
        thumb = np.asarray(im.convert("L").resize((64, 64), Image.BILINEAR), dtype=np.uint8)
    return Path(img_path).stem, h, thumb


if __name__ == "__main__":
    WORK.mkdir(parents=True, exist_ok=True)
    src = os.environ.get("DATA_SRC")
    if not src:
        hits = sorted(Path("/kaggle/input").rglob("RDD2022_all_countries"))
        if not hits:
            raise SystemExit("RDD2022_all_countries not found under /kaggle/input")
        src = str(hits[0])
    items, missing = prep.find_items(Path(src))
    log(f"{len(items)} labeled images ({missing} xml without image), workers {WORKERS}")
    with Pool(WORKERS) as pool:
        res = pool.map(one, [img for _, img in items], chunksize=64)
    order = sorted(range(len(res)), key=lambda i: res[i][0])
    stems = np.array([res[i][0] for i in order])
    dh = np.array([res[i][1] for i in order], dtype=np.uint64)
    thumbs = np.stack([res[i][2] for i in order])
    np.savez_compressed(WORK / "thumbs.npz", stems=stems, dhash=dh, thumbs=thumbs)
    log(f"saved thumbs.npz: {thumbs.shape}, {(WORK / 'thumbs.npz').stat().st_size / 1e6:.0f} MB")
