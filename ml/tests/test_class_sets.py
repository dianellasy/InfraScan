"""prepare_rdd2022.use_class_set: rdd4 keeps ids 0-3, 'pothole' keeps only potholes as class 0."""
import prepare_rdd2022 as P


def make(tmp_path):
    lab = tmp_path / "labels" / "train"
    lab.mkdir(parents=True)
    (lab / "a.txt").write_text("0 .5 .5 .1 .1\n3 .5 .5 .2 .2\n4 .1 .1 .1 .1\n")
    (lab / "b.txt").write_text("1 .5 .5 .1 .1\n")
    return lab


def test_rdd4_drops_manhole_only(tmp_path):
    lab = make(tmp_path)
    yaml, removed = P.use_class_set(tmp_path, "rdd4")
    assert removed == 1
    assert (lab / "a.txt").read_text() == "0 .5 .5 .1 .1\n3 .5 .5 .2 .2\n"
    assert (lab / "b.txt").read_text() == "1 .5 .5 .1 .1\n"
    assert "3: Pothole" in yaml.read_text()


def test_pothole_only_remaps_to_zero(tmp_path):
    lab = make(tmp_path)
    yaml, removed = P.use_class_set(tmp_path, "pothole")
    assert removed == 3
    assert (lab / "a.txt").read_text() == "0 .5 .5 .2 .2\n"
    assert (lab / "b.txt").read_text() == ""
    names = yaml.read_text().split("names:")[1]
    assert names.strip() == "0: Pothole"


def test_class_ids_never_move():
    """ids 0-3 must stay the same in rdd4 and rdd5 (label_map in backend/inference/service.py depends on them)."""
    assert P.CLASS_SETS["rdd5"][:4] == P.CLASS_SETS["rdd4"]
