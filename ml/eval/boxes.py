"""Box helpers shared by evaluate.py, analyze_preds.py, error_sheets.py and ensemble.py (one copy of the matching rule).

Matching rule used everywhere: detections of one class, sorted by confidence (highest first, stable), each
takes the free label box of that class with the highest IoU; it is a true positive if that IoU >= 0.5.
Each label box is matched at most once. Because higher-confidence detections are matched first, the result
for the detections above any confidence threshold does not depend on the ones below it: match once at the
lowest confidence, then count at each threshold.
"""

from pathlib import Path

import numpy as np


def read_yolo_labels(path, w, h):
    """YOLO label file -> list of (cls, x1, y1, x2, y2) in pixels; [] if the file is missing."""
    p = Path(path)
    boxes = []
    if p.exists():
        for line in p.read_text().split("\n"):
            if line.strip():
                c, xc, yc, bw, bh = map(float, line.split())
                boxes.append((int(c), (xc - bw / 2) * w, (yc - bh / 2) * h, (xc + bw / 2) * w, (yc + bh / 2) * h))
    return boxes


def iou_matrix(a, b):
    """IoU of every box in a (n, 4+) against every box in b (m, 4+), xyxy -> (n, m)."""
    a, b = np.asarray(a, dtype=float).reshape(-1, 4), np.asarray(b, dtype=float).reshape(-1, 4)
    ix = np.clip(np.minimum(a[:, None, 2], b[None, :, 2]) - np.maximum(a[:, None, 0], b[None, :, 0]), 0, None)
    iy = np.clip(np.minimum(a[:, None, 3], b[None, :, 3]) - np.maximum(a[:, None, 1], b[None, :, 1]), 0, None)
    inter = ix * iy
    area_a = (a[:, 2] - a[:, 0]) * (a[:, 3] - a[:, 1])
    area_b = (b[:, 2] - b[:, 0]) * (b[:, 3] - b[:, 1])
    return inter / np.maximum(area_a[:, None] + area_b[None, :] - inter, 1e-9)


def greedy_match(det, gt, iou_thr=0.5):
    """Match one class's detections to its label boxes.

    det: (n, 5+) rows [x1, y1, x2, y2, conf, ...]; gt: (m, 4) rows [x1, y1, x2, y2].
    Returns (order, tp, gt_idx, iou) for the detections sorted by confidence (order indexes det):
    tp[i] True if matched, gt_idx[i] the matched label box (-1 if none), iou[i] the best IoU with a label box
    still free at that point (the matched one if tp; may be below iou_thr if not; 0 if no box was free).
    """
    det = np.asarray(det, dtype=float)
    det = det if det.size else np.zeros((0, 5))
    gt = np.asarray(gt, dtype=float).reshape(-1, 4)
    order = np.argsort(-det[:, 4], kind="stable")
    n = len(order)
    tp, gt_idx, best = np.zeros(n, dtype=bool), np.full(n, -1), np.zeros(n)
    if n and len(gt):
        ious = iou_matrix(det[order, :4], gt)
        used = np.zeros(len(gt), dtype=bool)
        for i in range(n):
            free = np.nonzero(~used)[0]
            if not len(free):
                break
            j = free[np.argmax(ious[i, free])]
            best[i] = ious[i, j]
            if ious[i, j] >= iou_thr:
                tp[i], gt_idx[i], used[j] = True, j, True
    return order, tp, gt_idx, best
