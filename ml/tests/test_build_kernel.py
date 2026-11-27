"""Every run in ml/kaggle/runs.json builds into a kernel with the right settings and sources."""
import ast
import json

import build_kernel as bk

RUNS = json.loads(bk.RUNS.read_text(encoding="utf-8"))["runs"]


def test_every_run_builds():
    for name, r in RUNS.items():
        out = bk.build_run(name)
        code = (out / "run.py").read_text(encoding="utf-8")
        ast.parse(code)  # valid Python
        assert f"os.environ.setdefault('RUN', {name!r})" in code
        for k, v in r.get("env", {}).items():
            assert f"os.environ.setdefault({k!r}, {str(v)!r})" in code
        meta = json.loads((out / "kernel-metadata.json").read_text(encoding="utf-8"))
        assert meta["id"] == r["kernel"] and meta["enable_gpu"] == "true" and meta["is_private"] == "true"
        attached = meta["kernel_sources"] + meta["dataset_sources"]
        assert sorted(attached) == sorted(bk.SOURCES[s][1] for s in r["sources"])


def test_only_needed_modules_embedded():
    assert bk.needed_modules(bk.TRAIN_BODY.read_text(encoding="utf-8")) == [
        "prepare_rdd2022.py", "evaluate.py", "boxes.py"]  # evaluate pulls in boxes
    assert bk.needed_modules("import evaluate\n") == ["evaluate.py", "boxes.py"]
    # the data kernels run the prep script as a subprocess, by file name
    data_body = (bk.KAGGLE / "data_v1" / "body.py").read_text(encoding="utf-8")
    assert bk.needed_modules(data_body) == ["prepare_rdd2022.py"]


def test_extra_data_runs_attach_cmird():
    for name, r in RUNS.items():
        if r.get("env", {}).get("EXTRA") == "cmird":
            assert "cmird" in r["sources"], name
