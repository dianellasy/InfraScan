"""boxes.py: the one shared matching rule, checked against the plain-Python version it replaced."""
import numpy as np

import boxes
import evaluate


def old_own_counts(dets, gts, n_classes, t, iou_thr=0.5):
    """The pre-boxes.py evaluate.own_metrics loop for one threshold: match again from scratch at t."""
    def iou(a, b):
        ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
        iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
        u = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - ix * iy
        return ix * iy / u if u > 0 else 0.0

    tp, fp = [0] * n_classes, [0] * n_classes
    for d, gt in zip(dets, gts):
        d = d[d[:, 4] >= t]
        used = set()
        for k in np.argsort(-d[:, 4], kind="stable"):
            c = int(d[k, 5])
            best, bj = 0.0, -1
            for j, g in enumerate(gt):
                if j not in used and g[0] == c and iou(d[k, :4], g[1:]) > best:
                    best, bj = iou(d[k, :4], g[1:]), j
            if best >= iou_thr:
                tp[c] += 1
                used.add(bj)
            else:
                fp[c] += 1
    return tp, fp


def random_image(rng, n_classes=4):
    gt = []
    for _ in range(int(rng.integers(0, 6))):
        x, y = rng.uniform(0, 80, 2)
        w, h = rng.uniform(5, 30, 2)
        gt.append((int(rng.integers(0, n_classes)), x, y, x + w, y + h))
    det = []
    for _ in range(int(rng.integers(0, 12))):
        if gt and rng.random() < 0.6:  # near a label box
            c, x1, y1, x2, y2 = gt[int(rng.integers(0, len(gt)))]
            j = rng.normal(0, 4, 4)
            det.append([x1 + j[0], y1 + j[1], x2 + j[2], y2 + j[3], rng.random(), c if rng.random() < .8 else 0])
        else:
            x, y = rng.uniform(0, 80, 2)
            det.append([x, y, x + rng.uniform(5, 30), y + rng.uniform(5, 30), rng.random(), int(rng.integers(0, n_classes))])
    return np.array(det, float).reshape(-1, 6), gt


def test_iou_matrix():
    m = boxes.iou_matrix([[0, 0, 10, 10], [20, 20, 30, 30]], [[0, 0, 10, 10], [5, 0, 15, 10]])
    assert np.allclose(m, [[1, 1 / 3], [0, 0]])


def test_greedy_match_basic():
    det = [[0, 0, 10, 10, .5, 3], [0, 0, 10, 10, .9, 3], [50, 50, 60, 60, .7, 3]]
    order, tp, gt_idx, best = boxes.greedy_match(det, [[0, 0, 10, 10]])
    assert order.tolist() == [1, 2, 0]  # by confidence
    assert tp.tolist() == [True, False, False]  # the .5 duplicate finds its box already taken
    assert gt_idx.tolist() == [0, -1, -1] and best.tolist() == [1.0, 0.0, 0.0]
    order, tp, _, _ = boxes.greedy_match([], [[0, 0, 10, 10]])
    assert len(order) == 0 and len(tp) == 0


def test_own_metrics_same_as_old_loop():
    rng = np.random.default_rng(0)
    images = [random_image(rng) for _ in range(300)]
    preds = {i: {"w": 100, "h": 100, "det": d} for i, (d, _) in enumerate(images)}
    gts = {i: g for i, (_, g) in enumerate(images)}
    real_read = evaluate.read_labels
    evaluate.read_labels = lambda p, w, h: gts[p]
    try:
        new = evaluate.own_metrics(preds, lambda p: p, 4)
    finally:
        evaluate.read_labels = real_read
    for t, row in new.items():
        tp, fp = old_own_counts([d for d, _ in images], [g for _, g in images], 4, t)
        assert [row[c]["tp"] for c in range(4)] == tp and [row[c]["fp"] for c in range(4)] == fp
