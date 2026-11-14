"""Evaluation helpers for InfraScan road damage models.

Gives, on one split of the prepared dataset:
- Ultralytics val metrics overall and per class (P, R, AP50, AP50-95)
- the same per country (uses manifest.csv to know each image's country)
- our own matching at IoU 0.5: per class precision, recall, F1 and mean IoU of
  true positives at several confidence thresholds (pothole table included)

Matching rule (own metrics): greedy by confidence, same class, IoU >= 0.5,
each ground truth box matched at most once.
"""

import csv
import json
from collections import defaultdict
from pathlib import Path

import numpy as np

THRESHOLDS = [0.15, 0.25, 0.35, 0.5, 0.65]


def read_labels(lbl_path, w, h):
    boxes = []
    p = Path(lbl_path)
    if p.exists():
        for line in p.read_text().split("\n"):
            if not line.strip():
                continue
            c, xc, yc, bw, bh = map(float, line.split())
            boxes.append((int(c), (xc - bw / 2) * w, (yc - bh / 2) * h, (xc + bw / 2) * w, (yc + bh / 2) * h))
    return boxes


def iou(a, b):
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    u = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / u if u > 0 else 0.0


def collect_predictions(model, image_paths, imgsz, batch=32, device=0, augment=False):
    """Run the model once at conf 0.001 and keep all boxes per image."""
    preds = {}
    for i in range(0, len(image_paths), batch):
        chunk = image_paths[i:i + batch]
        res = model.predict(chunk, imgsz=imgsz, conf=0.001, iou=0.7, device=device, verbose=False,
                            augment=augment, max_det=300)
        for p, r in zip(chunk, res):
            h, w = r.orig_shape
            d = r.boxes.data.cpu().numpy() if len(r.boxes) else np.zeros((0, 6))
            preds[p] = {"w": w, "h": h, "det": d}
    return preds


def own_metrics(preds, label_dir_of, n_classes, thresholds=THRESHOLDS, iou_thr=0.5):
    """Per class P, R, F1 and mean IoU of matched boxes at each confidence threshold."""
    out = {}
    gts = {p: read_labels(label_dir_of(p), v["w"], v["h"]) for p, v in preds.items()}
    for t in thresholds:
        tp = np.zeros(n_classes)
        fp = np.zeros(n_classes)
        n_gt = np.zeros(n_classes)
        ious = defaultdict(list)
        for p, v in preds.items():
            gt = gts[p]
            for g in gt:
                n_gt[g[0]] += 1
            d = v["det"]
            d = d[d[:, 4] >= t] if len(d) else d
            order = np.argsort(-d[:, 4]) if len(d) else []
            used = set()
            for k in order:
                x1, y1, x2, y2, conf, c = d[k]
                c = int(c)
                best, bj = 0.0, -1
                for j, g in enumerate(gt):
                    if j in used or g[0] != c:
                        continue
                    v_iou = iou((x1, y1, x2, y2), g[1:])
                    if v_iou > best:
                        best, bj = v_iou, j
                if best >= iou_thr:
                    tp[c] += 1
                    used.add(bj)
                    ious[c].append(best)
                else:
                    fp[c] += 1
        row = {}
        for c in range(n_classes):
            prec = tp[c] / (tp[c] + fp[c]) if tp[c] + fp[c] else 0.0
            rec = tp[c] / n_gt[c] if n_gt[c] else 0.0
            f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
            row[c] = {"precision": round(prec, 4), "recall": round(rec, 4), "f1": round(f1, 4),
                      "mean_iou_tp": round(float(np.mean(ious[c])), 4) if ious[c] else None,
                      "tp": int(tp[c]), "fp": int(fp[c]), "gt": int(n_gt[c])}
        out[t] = row
    return out


def ultra_val(model, data_yaml, split, imgsz, device=0, batch=32, name="val"):
    m = model.val(data=data_yaml, split=split, imgsz=imgsz, batch=batch, device=device, plots=True,
                  name=name, verbose=False)
    names = m.names
    res = {"overall": {"precision": float(m.box.mp), "recall": float(m.box.mr),
                       "mAP50": float(m.box.map50), "mAP50_95": float(m.box.map)}, "per_class": {}}
    for i, c in enumerate(m.box.ap_class_index):
        p, r, ap50, ap = m.box.class_result(i)
        res["per_class"][names[int(c)]] = {"precision": float(p), "recall": float(r),
                                           "AP50": float(ap50), "AP50_95": float(ap)}
    res["save_dir"] = str(m.save_dir)
    return res


def per_country_val(model, data_root, manifest_csv, split, imgsz, device=0, work_dir="/kaggle/temp/country_lists"):
    """Run Ultralytics val once per country using image list files."""
    work = Path(work_dir)
    work.mkdir(parents=True, exist_ok=True)
    by_c = defaultdict(list)
    with open(manifest_csv) as f:
        for r in csv.DictReader(f):
            if r["split"] == split:
                by_c[r["country"]].append(str(Path(data_root) / "images" / split / f"{r['stem']}.jpg"))
    names = model.names
    out = {}
    for c, imgs in sorted(by_c.items()):
        lst = work / f"{split}_{c}.txt"
        lst.write_text("\n".join(imgs) + "\n")
        y = work / f"{split}_{c}.yaml"
        y.write_text(f"path: {data_root}\ntrain: {lst}\nval: {lst}\nnames:\n" +
                     "".join(f"  {i}: {n}\n" for i, n in names.items()))
        m = model.val(data=str(y), split="val", imgsz=imgsz, device=device, plots=False, verbose=False,
                      name=f"country_{split}_{c}")
        row = {"images": len(imgs), "precision": float(m.box.mp), "recall": float(m.box.mr),
               "mAP50": float(m.box.map50), "mAP50_95": float(m.box.map), "per_class_AP50": {}}
        for i, ci in enumerate(m.box.ap_class_index):
            row["per_class_AP50"][names[int(ci)]] = float(m.box.class_result(i)[2])
        out[c] = row
    return out


def full_report(model, data_root, data_yaml, manifest_csv, split, imgsz, out_json, device=0):
    data_root = Path(data_root)
    rep = {"split": split, "imgsz": imgsz}
    rep["ultralytics"] = ultra_val(model, data_yaml, split, imgsz, device=device, name=f"val_{split}")
    rep["per_country"] = per_country_val(model, str(data_root), manifest_csv, split, imgsz, device=device)
    imgs = sorted(str(p) for p in (data_root / "images" / split).glob("*.jpg"))
    preds = collect_predictions(model, imgs, imgsz, device=device)

    def lbl(p):
        return data_root / "labels" / split / (Path(p).stem + ".txt")

    om = own_metrics(preds, lbl, len(model.names))
    rep["own_iou50"] = {str(t): {model.names[c]: v for c, v in row.items()} for t, row in om.items()}
    Path(out_json).write_text(json.dumps(rep, indent=1))
    return rep
