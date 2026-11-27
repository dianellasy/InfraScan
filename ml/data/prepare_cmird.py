"""Build a train-only add-on of Indian pothole frames from CMIRD.

CMIRD = Custom Multitask Indian Road Dataset (Kaggle: ammanaveen/custom-multitask-indian-road-dataset-cmird,
CC BY-NC-SA 4.0, non-commercial). Facts checked by hand on 15 Nov 2026 (summary in ml/NEXT_DATA.md):

- Detection labels are YOLO txt with 10 classes in alphabetical order of the names on the Kaggle page:
  0 auto-rickshaw, 1 bike, 2 bus, 3 car, 4 crack, 5 manhole, 6 person, 7 pothole, 8 speed bump,
  9 waterlogging. Confirmed by looking at sample boxes of every class.
- `video_NN_frame_*` files are frames of 24 Indian (Kerala-style) dashcam/bike videos.
  `anomaly_vlcsnap-*` files (640x360, orange car bonnet) are Italian roads, not Indian: dropped.
- The val2017 folder's video frames are byte-identical copies of train2017 files: only train2017 is used.

What this script writes (only pothole frames, only for TRAINING; never for val/test):
- images/train/cmird_<stem>.jpg, resized so the long side is at most MAX_SIDE (1280, as in RDD2022 prep;
  resized with BILINEAR here, LANCZOS there).
- labels/train/cmird_<stem>.txt with pothole boxes as class 3 (the rdd5/rdd4 id), so
  prepare_rdd2022.use_class_set works on it unchanged (rdd4 keeps id 3; "pothole" maps it to 0).
- cmird_manifest.csv: per kept frame, its video, pothole and crack box counts (cracks are NOT written as
  labels: CMIRD has one "crack" class and RDD2022 has three crack types; the training body decides
  whether to keep frames that contain cracks).
- cmird_stats.json: counts, what was dropped and why.

Leakage guards:
1. Near-duplicate frames inside one video (consecutive frames are almost the same picture) are thinned:
   a frame is skipped if its 64x64 grayscale thumbnail differs from the last kept frame of the same video
   by less than --min-change mean absolute pixel difference.
2. Every kept frame is compared to every RDD2022 val/test image (dHash distance <= 4 as candidate,
   thumbnail mean abs diff < 5 to confirm, same rule as prepare_rdd2022). Matches are dropped.
   Needs the thumbs.npz written by the dup-analysis kernel and the data_v1 manifest.csv.

Usage:
  python prepare_cmird.py --src <CMIRD root containing 'Final annotations'> --out <dir>
                          --thumbs thumbs.npz --manifest manifest.csv [--min-change 6]
"""

import argparse
import csv
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
from PIL import Image

from prepare_rdd2022 import DUP_DIST, DUP_MAD, MAX_SIDE, dhash, popcount64

CMIRD_POTHOLE, CMIRD_CRACK = 7, 4
OUT_POTHOLE_ID = 3  # pothole id in rdd4/rdd5


def find_root(src):
    hits = sorted(Path(src).rglob("detection-object"))
    if not hits:
        raise SystemExit(f"no 'detection-object' folder under {src}")
    return hits[0].parent


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--thumbs", required=True, help="thumbs.npz (stems, dhash, thumbs) of all RDD2022 images")
    ap.add_argument("--manifest", required=True, help="data_v1 manifest.csv (which RDD2022 images are val/test)")
    ap.add_argument("--min-change", type=float, default=6.0,
                    help="skip a frame if it differs from the last kept frame of its video by less than this "
                         "(mean abs diff of 64x64 grayscale, 0..255)")
    args = ap.parse_args()

    root = find_root(args.src)
    out = Path(args.out)
    (out / "images" / "train").mkdir(parents=True, exist_ok=True)
    (out / "labels" / "train").mkdir(parents=True, exist_ok=True)
    stats = Counter()

    # RDD2022 val/test images we must not leak into
    with open(args.manifest, newline="") as f:
        held = {r["stem"] for r in csv.DictReader(f) if r["split"] in ("val", "test")}
    z = np.load(args.thumbs, allow_pickle=True)
    sel = np.array([s in held for s in z["stems"]])
    rdd_hash = z["dhash"][sel].astype(np.uint64)
    rdd_thumbs = z["thumbs"][sel].astype(np.float32)
    stats["rdd_val_test_images_checked_against"] = int(sel.sum())

    # group train2017 video frames by video, in frame order
    videos = defaultdict(list)
    for p in sorted((root / "images" / "train2017").iterdir()):
        m = re.match(r"(video_\d+)_frame_(\d+)$", p.stem)
        if not m:
            stats["dropped_not_indian_video (anomaly_vlcsnap, Italy)"] += 1
            continue
        videos[m.group(1)].append((int(m.group(2)), p))

    rows, per_video = [], {}
    for vid in sorted(videos):
        last = None
        kept = Counter()
        for _, p in sorted(videos[vid]):
            lab = root / "detection-object" / "labels" / "train2017" / f"{p.stem}.txt"
            boxes = [ln.split() for ln in lab.read_text().splitlines() if ln.strip()] if lab.exists() else []
            pot = [b for b in boxes if int(b[0]) == CMIRD_POTHOLE]
            n_crack = sum(1 for b in boxes if int(b[0]) == CMIRD_CRACK)
            if not pot:
                stats["frames_without_pothole (skipped)"] += 1
                continue
            with Image.open(p) as im:
                im = im.convert("RGB")
                small = np.asarray(im.convert("L").resize((64, 64), Image.BILINEAR), dtype=np.float32)
                h = np.uint64(dhash(im))
                if last is not None and float(np.abs(small - last).mean()) < args.min_change:
                    stats["pothole_frames_skipped_near_copy_of_previous_frame"] += 1
                    kept["near_copy"] += 1
                    continue
                d = popcount64(rdd_hash ^ h)
                cand = np.where(d <= DUP_DIST)[0]
                if len(cand) and min(float(np.abs(rdd_thumbs[i] - small).mean()) for i in cand) < DUP_MAD:
                    stats["pothole_frames_dropped_duplicate_of_rdd_val_test"] += 1
                    continue
                stats["dhash_candidates_vs_rdd_val_test"] += int(len(cand))
                last = small
                w, hgt = im.size
                if max(w, hgt) > MAX_SIDE:
                    s = MAX_SIDE / max(w, hgt)
                    im = im.resize((round(w * s), round(hgt * s)), Image.BILINEAR)
                name = f"cmird_{p.stem}"
                im.save(out / "images" / "train" / f"{name}.jpg", quality=95)
            (out / "labels" / "train" / f"{name}.txt").write_text(
                "".join(f"{OUT_POTHOLE_ID} {' '.join(b[1:5])}\n" for b in pot))
            rows.append({"stem": name, "video": vid, "w": im.size[0], "h": im.size[1],
                         "n_pothole": len(pot), "n_crack": n_crack})
            kept["frames"] += 1
            kept["boxes"] += len(pot)
            kept["frames_with_crack"] += n_crack > 0
        per_video[vid] = dict(kept)

    with open(out / "cmird_manifest.csv", "w", newline="") as f:
        wr = csv.DictWriter(f, fieldnames=["stem", "video", "w", "h", "n_pothole", "n_crack"])
        wr.writeheader()
        wr.writerows(rows)
    summary = {
        "source": "Kaggle ammanaveen/custom-multitask-indian-road-dataset-cmird (CC BY-NC-SA 4.0)",
        "use": "train only, pothole boxes as class 3",
        "kept_frames": len(rows),
        "kept_pothole_boxes": sum(r["n_pothole"] for r in rows),
        "kept_frames_with_crack_boxes": sum(1 for r in rows if r["n_crack"] > 0),
        "videos_used": sum(1 for v in per_video.values() if v.get("frames")),
        "min_change": args.min_change,
        "counts": dict(stats),
        "per_video": per_video,
    }
    (out / "cmird_stats.json").write_text(json.dumps(summary, indent=1))
    print(json.dumps({k: v for k, v in summary.items() if k != "per_video"}, indent=1))


if __name__ == "__main__":
    main()
