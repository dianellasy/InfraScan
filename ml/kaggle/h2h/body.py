# Head-to-head: our YOLO26-M 1024 (v1-main) vs Adarsh's e7 (YOLO26s 640) on the
# v1 test split, classes 0-3 only. Scored per country group: India, US, India+US, all.
# Caveat: e7's training split is not published, so it may have trained on some of these images.
import csv
import json
import os
import subprocess
import sys
import tarfile
import time
from pathlib import Path

t0 = time.time()
WORK, TMP = Path("/kaggle/working"), Path("/kaggle/temp")
TMP.mkdir(parents=True, exist_ok=True)


def log(m):
    print(f"[{time.time() - t0:6.0f}s] {m}", flush=True)


try:
    import ultralytics
    assert ultralytics.__version__ == "8.4.210"
except Exception:  # noqa: BLE001
    subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", "ultralytics==8.4.210"])
from ultralytics import YOLO  # noqa: E402

inp = Path("/kaggle/input")
tar = sorted(inp.rglob("rdd2022_v1.tar"))[0]
v1_w = [p for p in inp.rglob("best.pt") if "v1_yolo26m_1024" in str(p)][0]
e7_w = sorted(inp.rglob("e7_best.pt"))[0]
man = [p for p in inp.rglob("manifest.csv") if "infrascan-v1-main" in str(p) and "data_report_prep" not in str(p)][0]
log(f"v1 {v1_w}\ne7 {e7_w}\nmanifest {man}")
rows = [r for r in csv.DictReader(open(man)) if r["split"] == "test"]
want = {r["stem"]: r["country"] for r in rows}
src = TMP / "src"
with tarfile.open(tar) as tf:
    members = [m for m in tf.getmembers() if Path(m.name).stem in want and "/labels/" in m.name or
               (Path(m.name).stem in want and "/images/" in m.name)]
    tf.extractall(src, members=members)
log(f"extracted {len(members)} files for {len(want)} test images")
found = {}
for p in src.rglob("*.jpg"):
    found[p.stem] = p
groups = {"India": ["India"], "United_States": ["United_States"], "India+US": ["India", "United_States"],
          "all": sorted(set(want.values()))}
names4 = ["Longitudinal crack", "Transverse crack", "Alligator crack", "Pothole"]
out = {"note": "v1 test split, classes 0-3 only; e7 may have trained on some of these images"}
for g, countries in groups.items():
    root = TMP / f"h2h_{g.replace('+', '_')}"
    (root / "images" / "test").mkdir(parents=True, exist_ok=True)
    (root / "labels" / "test").mkdir(parents=True, exist_ok=True)
    n = 0
    for stem, c in want.items():
        if c not in countries or stem not in found:
            continue
        img = found[stem]
        lbl = Path(str(img).replace("/images/", "/labels/")).with_suffix(".txt")
        dst = root / "images" / "test" / f"{stem}.jpg"
        if not dst.exists():
            os.symlink(img, dst)
        keep = [ln for ln in (lbl.read_text().split("\n") if lbl.exists() else []) if ln and not ln.startswith("4 ")]
        (root / "labels" / "test" / f"{stem}.txt").write_text("\n".join(keep) + ("\n" if keep else ""))
        n += 1
    (root / "data.yaml").write_text(f"path: {root}\ntrain: images/test\nval: images/test\nnames:\n" +
                                    "".join(f"  {i}: {nm}\n" for i, nm in enumerate(names4)))
    res = {"images": n}
    for tag, w, sz in (("v1_26m_1024", v1_w, 1024), ("e7_26s_640", e7_w, 640)):
        m = YOLO(str(w)).val(data=str(root / "data.yaml"), split="val", imgsz=sz, batch=32, device=0, plots=False,
                             verbose=False, project=str(WORK / "runs"), name=f"{g}_{tag}", exist_ok=True, conf=0.001)
        per = {names4[int(c)]: round(float(m.box.class_result(i)[2]), 4)
               for i, c in enumerate(m.box.ap_class_index) if int(c) < 4}
        pi = [i for i, c in enumerate(m.box.ap_class_index) if int(c) == 3]
        pot = m.box.class_result(pi[0]) if pi else None
        res[tag] = {"mAP50_cls0to3": round(sum(per.values()) / max(len(per), 1), 4), "AP50": per,
                    "pothole_P_R_at_bestF1": [round(float(pot[0]), 3), round(float(pot[1]), 3)] if pot else None}
    out[g] = res
    log(f"{g}: {json.dumps(res)}")
    (WORK / "h2h.json").write_text(json.dumps(out, indent=1))
log("done")
