"""Picture sheets of missed boxes and false alarms for one class, from saved predictions.

Reads <name>_preds.json (written by evaluate.full_report) and the split's images, and writes
missed.png / false_alarms.png (24 random examples each) plus errors.json (every error) to --out.
Missed boxes are drawn green with the best confidence the model gave any box overlapping them
(IoU >= 0.5); false alarms are drawn red with their confidence.

Matching rule: same as analyze_preds / evaluate.own_metrics (greedy by confidence, IoU >= 0.5).

Usage:
  python error_sheets.py eval_val_preds.json --images <data_root>/images/val --out <dir> [--cls 3] [--thr 0.2]
"""

import argparse
import json
import random
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from boxes import greedy_match, iou_matrix


def find_errors(preds, cls, thr):
    """preds: {stem: {"gt": [[cls, x1, y1, x2, y2]], "det": [[x1, y1, x2, y2, conf, cls]]}}."""
    misses, false_alarms = [], []
    for stem, r in preds.items():
        gt = np.array([g[1:5] for g in r["gt"] if int(g[0]) == cls], dtype=float).reshape(-1, 4)
        det_all = np.array([x for x in r["det"] if int(x[5]) == cls], dtype=float).reshape(-1, 6)
        det = det_all[det_all[:, 4] >= thr]
        order, tp, gt_idx, best = greedy_match(det, gt)
        for i in np.nonzero(~tp)[0]:
            x = det[order[i]]
            false_alarms.append({"stem": stem, "box": x[:4].tolist(), "conf": float(x[4]),
                                 "best_iou_with_label": float(best[i])})
        missed = np.setdiff1d(np.arange(len(gt)), gt_idx[tp])
        if len(missed):
            seen = iou_matrix(gt[missed], det_all[:, :4]) >= 0.5  # any box of this class, any confidence
            for k, j in enumerate(missed):
                conf = det_all[seen[k], 4]
                misses.append({"stem": stem, "box": gt[j].tolist(), "best_conf": float(conf.max()) if len(conf) else 0.0})
    return misses, false_alarms


def sheet(items, images, path, color, key, n=24, seed=0):
    pick = random.Random(seed).sample(items, min(n, len(items)))
    cols, cell, text_h = 6, 240, 22
    out = Image.new("RGB", (cols * cell, max(1, -(-len(pick) // cols)) * (cell + text_h)), "white")
    draw = ImageDraw.Draw(out)
    for k, e in enumerate(pick):
        with Image.open(Path(images) / f"{e['stem']}.jpg") as f:
            im = f.convert("RGB")
        ImageDraw.Draw(im).rectangle(e["box"], outline=color, width=max(3, im.size[0] // 150))
        x, y = (k % cols) * cell, (k // cols) * (cell + text_h)
        out.paste(im.resize((cell, cell)), (x, y))
        draw.text((x + 3, y + cell + 3), f"{e['stem']} {key} {e[key]:.2f}", fill="black")
    out.save(path)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("preds")
    ap.add_argument("--images", required=True, help="folder with the split's images (<stem>.jpg)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--cls", type=int, default=3, help="class id (3 = pothole in rdd4; 0 for a pothole-only model)")
    ap.add_argument("--thr", type=float, default=0.2)
    args = ap.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    misses, false_alarms = find_errors(json.load(open(args.preds)), args.cls, args.thr)
    print(f"class {args.cls} at conf >= {args.thr}: missed {len(misses)}, false alarms {len(false_alarms)}")
    sheet(misses, args.images, out / "missed.png", "lime", "best_conf")
    sheet(false_alarms, args.images, out / "false_alarms.png", "red", "conf")
    (out / "errors.json").write_text(json.dumps({"cls": args.cls, "thr": args.thr, "missed": misses,
                                                 "false_alarms": false_alarms}, indent=1))


if __name__ == "__main__":
    main()
