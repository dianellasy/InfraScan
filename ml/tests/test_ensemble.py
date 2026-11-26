"""Weighted box fusion, flip mapping and the photo-level count used for ensembles / TTA."""
import numpy as np

import ensemble as e


def test_one_source_is_unchanged():
    d = np.array([[10, 10, 50, 50, 0.9, 3], [100, 100, 150, 140, 0.4, 0]])
    out = e.wbf([d])
    assert np.allclose(out, d)


def test_agreeing_boxes_merge_into_weighted_mean():
    a = np.array([[10, 10, 50, 50, 0.8, 3]])
    b = np.array([[12, 10, 52, 50, 0.4, 3]])
    out = e.wbf([a, b])
    assert len(out) == 1
    x1 = (10 * 0.8 + 12 * 0.4) / 1.2
    assert np.isclose(out[0, 0], x1) and np.isclose(out[0, 4], 0.6)  # mean conf, both sources agree


def test_box_found_by_one_of_two_sources_loses_half_its_confidence():
    a = np.array([[10, 10, 50, 50, 0.8, 3]])
    out = e.wbf([a, np.zeros((0, 6))])
    assert np.isclose(out[0, 4], 0.4)


def test_classes_never_merge_and_far_boxes_stay_apart():
    a = np.array([[10, 10, 50, 50, 0.8, 3], [200, 200, 240, 240, 0.7, 3]])
    b = np.array([[10, 10, 50, 50, 0.6, 0]])
    out = e.wbf([a, b])
    assert len(out) == 3
    assert sorted(out[:, 5].tolist()) == [0, 3, 3]


def test_below_skip_is_dropped():
    out = e.wbf([np.array([[10, 10, 50, 50, 0.005, 3]])])
    assert len(out) == 0


def test_flip_back_is_its_own_inverse():
    d = np.array([[10.0, 5, 30, 25, 0.5, 3]])
    f = e.flip_back(d, 100)
    assert f[0, :4].tolist() == [70, 5, 90, 25]
    assert np.allclose(e.flip_back(f, 100), d)


def test_photo_level():
    raw = {"a": {"gt": [[3, 0, 0, 1, 1]], "det": [[0, 0, 1, 1, 0.3, 3]]},  # found
           "b": {"gt": [[3, 0, 0, 1, 1]], "det": [[0, 0, 1, 1, 0.1, 3]]},  # missed at 0.25
           "c": {"gt": [[0, 0, 0, 1, 1]], "det": [[0, 0, 1, 1, 0.9, 3]]},  # wrong (crack only)
           "d": {"gt": [], "det": [[0, 0, 1, 1, 0.9, 0]]}}  # crack box, not a pothole flag
    assert e.photo_level(raw, 3, 0.25) == (1, 1, 2)
