"""analyze_preds: metrics, size slices, bootstrap and calibration on hand-made predictions."""
import numpy as np
import pytest

import analyze_preds as ap

compute_ap = pytest.importorskip("ultralytics.utils.metrics").compute_ap


def img(gt, det, w=100, h=100):  # gt: [cls,x1,y1,x2,y2]; det: [x1,y1,x2,y2,conf,cls]
    return {"w": w, "h": h, "gt": np.array(gt, float).reshape(-1, 5), "det": np.array(det, float).reshape(-1, 6)}


def test_perfect_detections():
    ims = {"A_000001": img([[3, 10, 10, 30, 30]], [[10, 10, 30, 30, .9, 3]]),
           "A_000002": img([[3, 50, 50, 70, 70]], [[50, 50, 70, 70, .8, 3]]), "A_000003": img([], [])}
    o = ap.analyze(ims, 3, .25, 50)["overall"]
    assert abs(o["AP50"] - compute_ap(np.array([.5, 1.]), np.array([1., 1.]))[0]) < 1e-9
    assert o["precision"] == 1 and o["recall"] == 1 and o["false_alarms_per_clean_image"] == 0


def test_nothing_detected():
    o = ap.analyze({"A_000001": img([[3, 10, 10, 30, 30]], [])}, 3, .25, 20)["overall"]
    assert o["AP50"] == 0 and o["recall"] == 0


def test_ap_formula_matches_ultralytics():
    rng = np.random.default_rng(0)
    for _ in range(200):
        n = int(rng.integers(1, 60))
        conf, tp = rng.random(n), rng.random(n) < rng.random()
        n_gt = int(max(tp.sum(), 1) + rng.integers(0, 10))
        o = np.argsort(-conf, kind="stable")
        tpc, fpc = np.cumsum(tp[o]), np.cumsum(~tp[o])
        assert abs(compute_ap(tpc / n_gt, tpc / (tpc + fpc))[0] - ap.ap_from(conf, tp, n_gt)) < 1e-9


def test_false_alarms_per_clean_image():
    ims = {"A_000001": img([], [[1, 1, 9, 9, .6, 3], [20, 20, 29, 29, .3, 3]]),
           "A_000002": img([], [[1, 1, 9, 9, .2, 3]]),
           "A_000003": img([[3, 10, 10, 30, 30]], [[10, 10, 30, 30, .9, 3]])}
    assert abs(ap.analyze(ims, 3, .25, 20)["overall"]["false_alarms_per_clean_image"] - 1.0) < 1e-9


def test_recall_by_size():
    ims = {"A_000001": img([[3, 10, 10, 14, 14]], [[10, 10, 14, 14, .9, 3]]), "A_000002": img([[3, 10, 10, 90, 90]], [])}
    s = ap.analyze(ims, 3, .25, 20)["recall_by_box_size"]
    assert s["tiny (<0.2%)"]["recall"] == 1.0 and s["large (>4%)"]["recall"] == 0.0


def test_other_classes_ignored():
    ims = {"A_000001": img([[3, 10, 10, 30, 30], [0, 50, 50, 70, 70]],
                           [[10, 10, 30, 30, .9, 3], [50, 50, 70, 70, .9, 0], [1, 1, 5, 5, .9, 1]])}
    o = ap.analyze(ims, 3, .25, 20)["overall"]
    assert o["tp"] == 1 and o["fp"] == 0 and o["n_gt"] == 1


def test_bootstrap():
    rng = np.random.default_rng(1)
    ims = {}
    for k in range(120):
        g = [[3, 10, 10, 40, 40]] if rng.random() < .5 else []
        d = [[10, 10, 40, 40, float(rng.random()), 3]] if rng.random() < .7 else []
        d += [[60, 60, 80, 80, float(rng.random() * .5), 3]] if rng.random() < .3 else []
        ims[f"Z_{k * 7:06d}"] = img(g, d)
    a, b = ap.analyze(ims, 3, .25, 300, seed=3), ap.analyze(ims, 3, .25, 300, seed=3)
    lo, hi = a["bootstrap"]["ci95"]["AP50"]
    assert lo <= a["overall"]["AP50"] <= hi
    assert a["bootstrap"] == b["bootstrap"]
    d = ap.analyze(ims, 3, .25, 100, other_images=ims)["bootstrap"]["paired_diff_B_minus_A"]["AP50"]
    assert d["mean"] == 0 and d["ci95"] == [0, 0]


def test_calibration():
    ims = {f"Z_{k:06d}": img([[3, 10, 10, 30, 30]] if k % 2 == 0 else [], [[10, 10, 30, 30, .9, 3]]) for k in range(100)}
    assert abs(ap.analyze(ims, 3, .25, 20)["calibration"]["ece"] - 0.4) < 0.02
