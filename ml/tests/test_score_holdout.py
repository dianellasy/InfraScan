import csv
import json

import pytest

import score_holdout as sh


def test_e7_holdout_list_comes_from_runs_json():
    assert sh.holdout_videos("e7_yolo26s_cmird_holdout") == [
        "video_02", "video_04", "video_05", "video_11", "video_12", "video_20", "video_21"]


def test_run_without_holdout_is_refused(tmp_path):
    p = tmp_path / "runs.json"
    p.write_text(json.dumps({"runs": {"x": {"env": {"MODEL": "yolo26s.pt"}}}}))
    with pytest.raises(SystemExit):
        sh.holdout_videos("x", p)


def _manifest(tmp_path):
    p = tmp_path / "cmird_manifest.csv"
    with open(p, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["stem", "video", "w", "h", "n_pothole", "n_crack"])
        w.writerows([["a1", "video_01", 1280, 720, 1, 0], ["b1", "video_02", 1280, 720, 2, 0],
                     ["a2", "video_01", 1280, 720, 1, 0], ["c1", "video_03", 1280, 720, 1, 0]])
    return p


def test_only_held_out_videos_are_scored_with_video_as_unit(tmp_path):
    rows = sh.holdout_rows(_manifest(tmp_path), ["video_01", "video_03"])
    assert [r["stem"] for r in rows] == ["a1", "a2", "c1"]
    out = tmp_path / "holdout_manifest.csv"
    sh.write_unit_manifest(rows, out)
    assert out.read_text().split() == ["stem,unit", "a1,video_01", "a2,video_01", "c1,video_03"]


def test_unknown_video_is_refused(tmp_path):
    with pytest.raises(SystemExit):
        sh.holdout_rows(_manifest(tmp_path), ["video_01", "video_99"])
