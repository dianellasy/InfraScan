"""Offline analysis of saved predictions (<name>_preds.json, written by evaluate.full_report).

Answers the questions a single mAP number cannot:
- How sure are we? Confidence intervals by *cluster bootstrap*: whole split units (blocks of
  neighbouring frames) are resampled, not single images, because frames of one drive are correlated.
- Is run B really better than run A? Paired bootstrap on the same units (--compare).
- Where does it fail? Recall by box size (share of image area), per country.
- False alarms per clean image (images with no label of any kept class), at the chosen threshold.
- Is the confidence meaningful? Reliability table and expected calibration error (ECE).

Matching: greedy by confidence, same class, IoU >= 0.5, each ground-truth box once. One implementation,
boxes.greedy_match, is shared with evaluate.own_metrics and error_sheets (tests/test_boxes.py checks it
against the older plain-Python loop).

AP50: the AP-from-curve step replicates compute_ap of the pinned Ultralytics 8.4.210 exactly
(random-case test, max difference 0). But the AP50 printed here is NOT identical to the AP50 in
eval_val.json: on the 12-photo cross-check it was 0.521 here vs 0.478 from Ultralytics. Likely cause
(not verified): Ultralytics' validator uses its own NMS (multi-label, conf 0.001) and matching, which
saved predictions cannot reproduce; predictions here are stored from conf 0.01. So: use this tool to
compare runs with each other and to get intervals; quote eval_val.json when comparing with the model card.

Usage:
  python analyze_preds.py eval_val_preds.json [--manifest manifest.csv] [--cls 3] [--thr 0.25]
                          [--boot 1000] [--compare other_preds.json] [--out report.json]
"""

import argparse
import csv
import json
import re
from pathlib import Path

import numpy as np

from boxes import greedy_match

BLOCK = 30
SIZE_EDGES = [0.0, 0.002, 0.01, 0.04, 1.01]  # box area as a share of the image
SIZE_NAMES = ["tiny (<0.2%)", "small (0.2-1%)", "medium (1-4%)", "large (>4%)"]


def load(path):
    raw = json.loads(Path(path).read_text())
    return {s: {"w": v["w"], "h": v["h"], "det": np.array(v["det"], dtype=float).reshape(-1, 6),
                "gt": np.array(v["gt"], dtype=float).reshape(-1, 5)} for s, v in raw.items()}


def country_of(stem):
    return stem.rsplit("_", 1)[0]


def units_of(stems, manifest=None):
    if manifest:
        with open(manifest) as f:
            m = {r["stem"]: r.get("unit") or r.get("group") for r in csv.DictReader(f)}
        if all(s in m and m[s] for s in stems):
            return {s: m[s] for s in stems}
    return {s: f"{country_of(s)}_{int(re.search(r'(\d+)$', s).group(1)) // BLOCK}" for s in stems}


def match_image(img, cls, iou_thr=0.5):
    """Greedy match. Returns (det conf, tp flags, gt area fractions, conf of the matching det per gt or 0)."""
    det = img["det"][img["det"][:, 5] == cls]
    gt = img["gt"][img["gt"][:, 0] == cls][:, 1:]
    order, tp, gt_idx, _ = greedy_match(det, gt, iou_thr)
    conf = det[order, 4]
    hit_conf = np.zeros(len(gt))
    hit_conf[gt_idx[tp]] = conf[tp]
    area = ((gt[:, 2] - gt[:, 0]) * (gt[:, 3] - gt[:, 1])) / (img["w"] * img["h"]) if len(gt) else np.zeros(0)
    return conf, tp, area, hit_conf


def ap_from(conf, tp, n_gt):
    """AP50 from detections (replicates Ultralytics 8.4.210 compute_ap)."""
    if n_gt == 0 or len(conf) == 0:
        return 0.0
    order = np.argsort(-conf, kind="stable")
    tpc = np.cumsum(tp[order])
    fpc = np.cumsum(~tp[order])
    recall, precision = tpc / n_gt, tpc / (tpc + fpc)
    # Same end-of-curve handling as Ultralytics 8.4.210 compute_ap: precision drops to 0 at the last
    # recall actually reached (the textbook version interpolates towards recall 1.0 and overstates AP).
    mrec = np.concatenate(([0.0], recall, [recall[-1]], [1.0]))
    mpre = np.concatenate(([1.0], precision, [0.0], [0.0]))
    mpre = np.flip(np.maximum.accumulate(np.flip(mpre)))
    x = np.linspace(0, 1, 101)
    y = np.interp(x, mrec, mpre)
    return float(np.sum((y[1:] + y[:-1]) / 2 * np.diff(x)))


def bag(images, stems, cls):
    """Pool per-image match results of the given stems into arrays."""
    conf, tp, area, hit, clean_conf, n_clean = [], [], [], [], [], 0
    for s in stems:
        img = images[s]
        c, t, a, h = match_image(img, cls)
        conf.append(c); tp.append(t); area.append(a); hit.append(h)
        if len(img["gt"]) == 0:  # clean image: no label of any kept class
            n_clean += 1
            clean_conf.append(c)
    cat = lambda xs, dt=float: np.concatenate(xs) if xs else np.zeros(0, dtype=dt)
    return {"conf": cat(conf), "tp": cat(tp, bool), "area": cat(area), "hit": cat(hit),
            "clean_conf": cat(clean_conf), "n_clean": n_clean, "n_img": len(stems)}


def merge(bags):
    out = {k: np.concatenate([b[k] for b in bags]) for k in ("conf", "tp", "area", "hit", "clean_conf")}
    out["n_clean"] = sum(b["n_clean"] for b in bags)
    out["n_img"] = sum(b["n_img"] for b in bags)
    return out


def metrics(b, thr):
    n_gt = len(b["area"])
    sel = b["conf"] >= thr
    tp, fp = int(b["tp"][sel].sum()), int((~b["tp"][sel]).sum())
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / n_gt if n_gt else 0.0
    return {"AP50": ap_from(b["conf"], b["tp"], n_gt), "precision": p, "recall": r,
            "F1": 2 * p * r / (p + r) if p + r else 0.0, "n_gt": n_gt, "tp": tp, "fp": fp,
            "false_alarms_per_clean_image": float((b["clean_conf"] >= thr).sum() / b["n_clean"]) if b["n_clean"] else None,
            "n_clean_images": b["n_clean"]}


def recall_by_size(b, thr):
    out = {}
    for name, lo, hi in zip(SIZE_NAMES, SIZE_EDGES[:-1], SIZE_EDGES[1:]):
        m = (b["area"] >= lo) & (b["area"] < hi)
        n = int(m.sum())
        out[name] = {"n_gt": n, "recall": float((b["hit"][m] >= thr).sum() / n) if n else None}
    return out


def calibration(b, bins=10, min_conf=0.05):
    sel = b["conf"] >= min_conf
    conf, tp = b["conf"][sel], b["tp"][sel]
    edges = np.linspace(min_conf, 1.0, bins + 1)
    rows, ece = [], 0.0
    for lo, hi in zip(edges[:-1], edges[1:]):
        m = (conf >= lo) & (conf < hi + (1e-9 if hi >= 1.0 else 0))
        if m.sum():
            gap = abs(conf[m].mean() - tp[m].mean())
            ece += m.sum() / len(conf) * gap
            rows.append({"conf_range": [round(lo, 2), round(hi, 2)], "n": int(m.sum()),
                         "mean_conf": round(float(conf[m].mean()), 3), "share_correct": round(float(tp[m].mean()), 3)})
    return {"ece": round(float(ece), 4), "bins": rows, "note": f"detections with conf >= {min_conf}; 'correct' = IoU>=0.5 match"}


def cluster_bootstrap(unit_bags, thr, n_boot, seed, other=None):
    """Resample units with replacement. Returns CI per metric (and paired difference vs `other` if given)."""
    rng = np.random.default_rng(seed)
    keys = sorted(unit_bags)
    names = ("AP50", "precision", "recall", "F1", "false_alarms_per_clean_image")
    draws, diffs = {k: [] for k in names}, {k: [] for k in names}
    for _ in range(n_boot):
        pick = [keys[i] for i in rng.integers(0, len(keys), len(keys))]
        m = metrics(merge([unit_bags[k] for k in pick]), thr)
        for k in names:
            if m[k] is not None:
                draws[k].append(m[k])
        if other is not None:
            m2 = metrics(merge([other[k] for k in pick]), thr)
            for k in names:
                if m[k] is not None and m2[k] is not None:
                    diffs[k].append(m2[k] - m[k])
    ci = lambda v: [round(float(np.percentile(v, 2.5)), 4), round(float(np.percentile(v, 97.5)), 4)] if len(v) else None
    res = {"units": len(keys), "n_boot": n_boot, "ci95": {k: ci(draws[k]) for k in names}}
    if other is not None:
        res["paired_diff_B_minus_A"] = {k: {"mean": round(float(np.mean(diffs[k])), 4), "ci95": ci(diffs[k]),
                                            "share_B_better": round(float(np.mean(np.array(diffs[k]) > 0)), 3)}
                                        for k in ("AP50", "recall", "F1") if diffs[k]}
    return res


def analyze(images, cls, thr, n_boot, manifest=None, other_images=None, seed=0):
    stems = sorted(images)
    unit = units_of(stems, manifest)
    by_unit = {}
    for s in stems:
        by_unit.setdefault(unit[s], []).append(s)
    unit_bags = {u: bag(images, ss, cls) for u, ss in by_unit.items()}
    other_bags = None
    if other_images is not None:
        common = sorted(set(images) & set(other_images))
        if len(common) != len(stems):
            raise SystemExit(f"--compare needs the same images (A {len(stems)}, common {len(common)})")
        other_bags = {u: bag(other_images, ss, cls) for u, ss in by_unit.items()}
    total = merge(list(unit_bags.values()))
    report = {"class": cls, "threshold": thr, "images": len(stems), "overall": metrics(total, thr),
              "recall_by_box_size": recall_by_size(total, thr), "calibration": calibration(total),
              "bootstrap": cluster_bootstrap(unit_bags, thr, n_boot, seed, other_bags), "per_country": {}}
    for c in sorted({country_of(s) for s in stems}):
        b = merge([bag(images, [s for s in stems if country_of(s) == c], cls)])
        report["per_country"][c] = {**metrics(b, thr), "recall_by_box_size": recall_by_size(b, thr)}
    return report


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("preds")
    ap.add_argument("--cls", type=int, default=3, help="class id (3 = pothole)")
    ap.add_argument("--thr", type=float, default=0.25)
    ap.add_argument("--boot", type=int, default=1000)
    ap.add_argument("--manifest", default=None)
    ap.add_argument("--compare", default=None, help="second run's preds.json: paired comparison (B minus A)")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    rep = analyze(load(a.preds), a.cls, a.thr, a.boot, a.manifest, load(a.compare) if a.compare else None)
    txt = json.dumps(rep, indent=1)
    if a.out:
        Path(a.out).write_text(txt)
    o, ci = rep["overall"], rep["bootstrap"]["ci95"]
    print(f"class {a.cls} at conf>={a.thr}: {rep['images']} images, {o['n_gt']} labelled, {rep['bootstrap']['units']} units")
    for k in ("AP50", "precision", "recall", "F1", "false_alarms_per_clean_image"):
        v = o[k]
        print(f"  {k:30s} {v if v is None else round(v, 3)}   95% CI {ci[k]}")
    print("  recall by size:", {k: (v['recall'] if v['recall'] is None else round(v['recall'], 2), v['n_gt']) for k, v in rep["recall_by_box_size"].items()})
    print("  ECE:", rep["calibration"]["ece"])
    for c, v in rep["per_country"].items():
        print(f"  {c}: n_gt {v['n_gt']}  AP50 {v['AP50']:.3f}  P {v['precision']:.2f}  R {v['recall']:.2f}  FA/clean img {v['false_alarms_per_clean_image']}")
    if "paired_diff_B_minus_A" in rep["bootstrap"]:
        print("  B minus A:", json.dumps(rep["bootstrap"]["paired_diff_B_minus_A"]))


if __name__ == "__main__":
    main()
