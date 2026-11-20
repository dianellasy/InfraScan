"""Combine models (ensemble) and test-time augmentation (TTA), on saved predictions. CPU only, no training.

Both ideas end the same way: several sets of boxes for the same photo are merged into one with weighted box
fusion (WBF; Solovyev et al. 2021, the usual merge step in road-damage challenge ensembles). WBF averages the
corners of overlapping boxes (weighted by confidence) instead of keeping one and throwing the rest away (NMS).
A box only one source of M found keeps 1/M of its confidence, so agreement between sources raises the score.

- Ensemble: fuse the saved <name>_preds.json of several models (written by evaluate.full_report).
- TTA: YOLO26 has no built-in augment=True (Ultralytics turns it off for end-to-end heads), so `tta` runs a
  model on each photo several ways (as is, mirrored left-right, larger input size), maps the boxes back to
  the original photo and saves one preds.json per way; then fuse those.

Prediction files: {stem: {"w", "h", "det": [[x1, y1, x2, y2, conf, cls], ...] (conf >= 0.01), "gt": [[cls, x1,
y1, x2, y2], ...]}}, pixels of the original photo - the same format analyze_preds.py reads.

Usage:
  python ensemble.py tta  --weights best.pt --images <val/images> --labels <val/labels> --out <dir> [--variants 640,640f,768]
  python ensemble.py fuse --out fused_preds.json a_preds.json b_preds.json ... [--iou 0.55]
  python ensemble.py compare --base e4_preds.json --manifest manifest.csv name=preds.json ...
"""

import argparse
import json
import time
from pathlib import Path

import numpy as np

from boxes import iou_matrix, read_yolo_labels
from evaluate import DUMP_MIN_CONF as SKIP, dump_entry


def wbf(dets, iou_thr=0.55, skip=SKIP):
    """Weighted box fusion of several detection sets for one photo.

    dets: list of M arrays (n_i, 6) [x1, y1, x2, y2, conf, cls]. Returns (k, 6), sorted by confidence.
    Per class, boxes from all sources are taken by confidence (highest first); each joins the first fused box of
    its class with IoU > iou_thr, else starts a new one. Fused corners = confidence-weighted mean of its boxes;
    fused confidence = sum of (weighted) confidences / total weight, capped as in the reference implementation
    (a cluster of more boxes than sources does not exceed the mean confidence).
    """
    weights = np.ones(len(dets))  # every source counts the same
    rows = []
    for i, d in enumerate(dets):
        d = np.asarray(d, dtype=float).reshape(-1, 6)
        d = d[d[:, 4] >= skip]
        if len(d):
            rows.append(np.column_stack([d, np.full(len(d), weights[i])]))
    if not rows:
        return np.zeros((0, 6))
    allb = np.concatenate(rows)  # x1 y1 x2 y2 conf cls weight
    out = []
    for c in np.unique(allb[:, 5]):
        b = allb[allb[:, 5] == c]
        b = b[np.argsort(-b[:, 4], kind="stable")]
        fused = []  # current fused box per cluster: x1 y1 x2 y2
        members = []  # list of row indices per cluster
        for i in range(len(b)):
            j = -1
            if fused:
                ious = iou_matrix(b[i:i + 1, :4], np.array(fused))[0]
                k = int(np.argmax(ious))
                if ious[k] > iou_thr:
                    j = k
            if j < 0:
                fused.append(b[i, :4].copy())
                members.append([i])
            else:
                members[j].append(i)
                mb = b[members[j]]
                wc = mb[:, 4] * mb[:, 6]
                fused[j] = (mb[:, :4] * wc[:, None]).sum(0) / wc.sum()
        for f, idx in zip(fused, members):
            mb = b[idx]
            conf = (mb[:, 4] * mb[:, 6]).sum() / mb[:, 6].sum()  # weighted mean confidence of the cluster
            conf *= min(mb[:, 6].sum(), weights.sum()) / weights.sum()  # found by fewer sources -> lower
            out.append([*f, conf, c])
    out = np.array(out).reshape(-1, 6)
    return out[np.argsort(-out[:, 4], kind="stable")]


def load_raw(path):
    return json.loads(Path(path).read_text())


def fuse_files(paths, iou_thr=0.55):
    """Fuse several preds.json files (same photos) into one dict in the same format."""
    srcs = [load_raw(p) for p in paths]
    stems = sorted(srcs[0])
    for p, s in zip(paths, srcs):
        if sorted(s) != stems:
            raise SystemExit(f"{p} has different photos ({len(s)} vs {len(stems)})")
    out = {}
    for st in stems:
        f = wbf([s[st]["det"] for s in srcs], iou_thr=iou_thr)
        out[st] = {"w": srcs[0][st]["w"], "h": srcs[0][st]["h"], "det": np.round(f, 3).tolist(), "gt": srcs[0][st]["gt"]}
    return out


def flip_back(det, w):
    """Boxes predicted on a left-right mirrored photo -> boxes on the original photo."""
    det = det.copy()
    x1 = w - det[:, 2]
    det[:, 2] = w - det[:, 0]
    det[:, 0] = x1
    return det


def run_tta(weights, images, labels, out_dir, variants, device="cpu", batch=16):
    """Predict every photo once per variant ("640" = imgsz 640; "640f" = imgsz 640 on the mirrored photo).

    Writes <out_dir>/<variant>_preds.json and returns seconds per photo per variant.
    """
    from PIL import Image
    from ultralytics import YOLO

    model = YOLO(str(weights))
    paths = sorted(Path(images).glob("*.jpg"))
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    speed = {}
    for v in variants:
        flip = v.endswith("f")
        imgsz = int(v.rstrip("f"))
        dump, t0 = {}, time.time()
        for i in range(0, len(paths), batch):
            chunk = paths[i:i + batch]
            ims = [np.asarray(Image.open(p).convert("RGB"))[:, :, ::-1] for p in chunk]  # BGR, as cv2 would load
            if flip:
                ims = [np.ascontiguousarray(im[:, ::-1]) for im in ims]
            res = model.predict(ims, imgsz=imgsz, conf=SKIP, iou=0.7, device=device, verbose=False, max_det=300)
            for p, r in zip(chunk, res):
                h, w = r.orig_shape
                d = r.boxes.data.cpu().numpy() if len(r.boxes) else np.zeros((0, 6))
                if flip and len(d):
                    d = flip_back(d, w)
                dump[p.stem] = dump_entry(d, w, h, read_yolo_labels(Path(labels) / f"{p.stem}.txt", w, h))
        speed[v] = round((time.time() - t0) / len(paths), 4)
        (out_dir / f"{v}_preds.json").write_text(json.dumps(dump))
        print(f"{v}: {len(dump)} photos, {speed[v]} s per photo", flush=True)
    (out_dir / "speed.json").write_text(json.dumps(speed, indent=1))
    return speed


def photo_level(raw, cls=3, thr=0.25):
    """Photo view: does the photo contain a pothole? found = flagged photos that have one; wrong = flagged
    photos without one; total = photos that have one."""
    found = wrong = total = 0
    for v in raw.values():
        has = any(int(g[0]) == cls for g in v["gt"])
        flagged = any(int(d[5]) == cls and d[4] >= thr for d in v["det"])
        total += has
        found += has and flagged
        wrong += flagged and not has
    return found, wrong, total


def compare(base, others, manifest=None, cls=3, n_boot=1000):
    """One row per run: own-matching AP50, P/R at 0.25, photo-level at 0.15/0.20/0.25, and paired difference
    vs base (cluster bootstrap, same split units)."""
    import analyze_preds as ap

    base_imgs = ap.load(base)
    rows = {}
    for name, path in [("base", base)] + list(others.items()):
        imgs = ap.load(path)
        o = ap.analyze(imgs, cls, 0.25, 0, manifest)["overall"]  # this run's own numbers (no bootstrap)
        raw = load_raw(path)
        row = {"AP50": round(o["AP50"], 3), "P@0.25": round(o["precision"], 3), "R@0.25": round(o["recall"], 3),
               "photos found/wrong": {t: photo_level(raw, cls, t)[:2] for t in (0.15, 0.20, 0.25)}}
        if path != base:
            rep = ap.analyze(base_imgs, cls, 0.25, n_boot, manifest, other_images=imgs)  # B = this run, A = base
            d = rep["bootstrap"]["paired_diff_B_minus_A"]
            row["AP50 vs base"] = {"diff": d["AP50"]["mean"], "ci95": d["AP50"]["ci95"]}
            row["recall@0.25 vs base"] = {"diff": d["recall"]["mean"], "ci95": d["recall"]["ci95"]}
        rows[name] = row
        print(name, json.dumps(row), flush=True)
    return rows


def main():
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("tta")
    t.add_argument("--weights", required=True)
    t.add_argument("--images", required=True)
    t.add_argument("--labels", required=True)
    t.add_argument("--out", required=True)
    t.add_argument("--variants", default="640,640f,768")
    t.add_argument("--device", default="cpu")
    f = sub.add_parser("fuse")
    f.add_argument("--out", required=True)
    f.add_argument("--iou", type=float, default=0.55)
    f.add_argument("preds", nargs="+")
    c = sub.add_parser("compare")
    c.add_argument("--base", required=True)
    c.add_argument("--manifest", default=None)
    c.add_argument("--boot", type=int, default=1000)
    c.add_argument("--out", default=None)
    c.add_argument("runs", nargs="+", help="name=preds.json")
    a = p.parse_args()
    if a.cmd == "tta":
        run_tta(a.weights, a.images, a.labels, a.out, a.variants.split(","), a.device)
    elif a.cmd == "fuse":
        Path(a.out).write_text(json.dumps(fuse_files(a.preds, a.iou)))
        print(f"wrote {a.out}")
    else:
        rows = compare(a.base, dict(r.split("=", 1) for r in a.runs), a.manifest, n_boot=a.boot)
        if a.out:
            Path(a.out).write_text(json.dumps(rows, indent=1))


if __name__ == "__main__":
    main()
