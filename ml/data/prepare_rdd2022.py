"""Prepare RDD2022 for YOLO training.

What it does (all from the real files, nothing assumed):
1. Finds every labeled training image (Pascal VOC XML) in the RDD2022 folder.
2. Records per country: image count, image sizes, label counts, other labels,
   whether test labels exist, and a brightness estimate (to spot night images).
3. Converts boxes to YOLO format with a fixed class order:
   0 D00, 1 D10, 2 D20, 3 D40. Other labels are dropped and counted.
4. Checks for bad boxes (outside the image, tiny, zero size) and reports them.
5. Makes a grouped, stratified train / val / test split (seed fixed).
   Group = country + block of BLOCK consecutive image ids, so frames that sit
   next to each other (often the same road) stay in the same split.
   Stratified by country and by "group has a pothole".
6. Writes images (only images wider than MAX_SIDE are resized; others are
   copied as is), YOLO labels, data.yaml, manifest.csv and stats.json.
7. Counts near-duplicate images across splits with a 64-bit dHash.

Run on Kaggle:  python prepare_rdd2022.py --src /kaggle/input/rdd2022 --out /kaggle/temp/rdd2022_yolo
"""

import argparse
import csv
import json
import os
import random
import re
import shutil
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from multiprocessing import Pool
from pathlib import Path

from PIL import Image, ImageOps

CLASSES = ["D00", "D10", "D20", "D40"]
CLASS_NAMES = ["Longitudinal crack", "Transverse crack", "Alligator crack", "Pothole"]
CLASS_ID = {c: i for i, c in enumerate(CLASSES)}
SEED = 42
BLOCK = int(os.environ.get("PREP_BLOCK", 30))  # consecutive ids per group
VAL_FRAC = 0.10
TEST_FRAC = 0.10
MAX_SIDE = 1280     # keep enough pixels for imgsz 1024 tests
TINY_FRAC = 0.0001  # box area below 0.01% of the image counts as tiny
DARK_MEAN = 60      # mean grey level below this counts as "dark" (night or very low light)


def country_of(stem):
    return stem.rsplit("_", 1)[0]


def id_of(stem):
    m = re.search(r"(\d+)$", stem)
    return int(m.group(1)) if m else 0


def find_items(src):
    """Return list of (xml_path, img_path) for labeled train images."""
    items = []
    missing = 0
    for xml in sorted(src.rglob("*.xml")):
        if "train" not in xml.parts:
            continue
        stem = xml.stem
        # layout: .../train/annotations/xmls/X.xml and .../train/images/X.jpg
        train_dir = xml.parents[2] if xml.parent.name == "xmls" else xml.parents[1]
        img = None
        for ext in (".jpg", ".jpeg", ".png", ".JPG"):
            p = train_dir / "images" / f"{stem}{ext}"
            if p.exists():
                img = p
                break
        if img is None:
            missing += 1
            continue
        items.append((xml, img))
    return items, missing


def parse_xml(xml):
    root = ET.parse(xml).getroot()
    objs = []
    for o in root.iter("object"):
        name = (o.findtext("name") or "").strip()
        bb = o.find("bndbox")
        if bb is None:
            objs.append((name, None))
            continue
        try:
            box = [float(bb.findtext(k)) for k in ("xmin", "ymin", "xmax", "ymax")]
        except (TypeError, ValueError):
            box = None
        objs.append((name, box))
    return objs


def dhash(img):
    g = img.convert("L").resize((9, 8), Image.BILINEAR)
    px = list(g.getdata())
    bits = 0
    for r in range(8):
        for c in range(8):
            bits = (bits << 1) | (px[r * 9 + c] > px[r * 9 + c + 1])
    return bits


def inspect_one(args):
    """Open one image, read its XML, build YOLO lines and checks."""
    xml, img_path = args
    stem = Path(img_path).stem
    out = {"stem": stem, "img": str(img_path), "country": country_of(stem)}
    with Image.open(img_path) as im:
        w, h = im.size
        out["w"], out["h"] = w, h
        small = im.convert("L").resize((64, 64), Image.BILINEAR)
        out["brightness"] = round(sum(small.getdata()) / (64 * 64), 1)
        out["dhash"] = dhash(im)
    counts = Counter()
    other = Counter()
    lines = []
    flags = Counter()
    for name, box in parse_xml(xml):
        if box is None:
            flags["no_box"] += 1
            continue
        if name not in CLASS_ID:
            other[name] += 1
            continue
        x1, y1, x2, y2 = box
        if x1 < 0 or y1 < 0 or x2 > w or y2 > h:
            flags["outside_image"] += 1
        x1, x2 = max(0.0, min(x1, x2)), min(float(w), max(x1, x2))
        y1, y2 = max(0.0, min(y1, y2)), min(float(h), max(y1, y2))
        bw, bh = x2 - x1, y2 - y1
        if bw < 1 or bh < 1:
            flags["zero_size_dropped"] += 1
            continue
        if bw * bh < TINY_FRAC * w * h:
            flags["tiny_kept"] += 1
        cid = CLASS_ID[name]
        counts[name] += 1
        lines.append(f"{cid} {(x1 + x2) / 2 / w:.6f} {(y1 + y2) / 2 / h:.6f} {bw / w:.6f} {bh / h:.6f}")
    out["counts"] = dict(counts)
    out["other"] = dict(other)
    out["flags"] = dict(flags)
    out["lines"] = lines
    return out


def write_one(args):
    rec, out_dir = args
    split = rec["split"]
    img_dst = Path(out_dir) / "images" / split / (rec["stem"] + ".jpg")
    lbl_dst = Path(out_dir) / "labels" / split / (rec["stem"] + ".txt")
    src = Path(rec["img"])
    if max(rec["w"], rec["h"]) > MAX_SIDE:
        with Image.open(src) as im:
            im = im.convert("RGB")
            s = MAX_SIDE / max(im.size)
            im = im.resize((round(im.size[0] * s), round(im.size[1] * s)), Image.LANCZOS)
            im.save(img_dst, quality=95)
    elif src.suffix.lower() in (".jpg", ".jpeg"):
        shutil.copyfile(src, img_dst)
    else:
        with Image.open(src) as im:
            im.convert("RGB").save(img_dst, quality=95)
    lbl_dst.write_text("\n".join(rec["lines"]) + ("\n" if rec["lines"] else ""))


def make_split(recs):
    rng = random.Random(SEED)
    groups = defaultdict(list)
    for r in recs:
        groups[(r["country"], id_of(r["stem"]) // BLOCK)].append(r)
    by_country = defaultdict(lambda: {True: [], False: []})
    for key, members in groups.items():
        has_pot = any(m["counts"].get("D40", 0) for m in members)
        by_country[key[0]][has_pot].append(key)
    assign = {}
    for country in sorted(by_country):
        for has_pot in (True, False):
            keys = sorted(by_country[country][has_pot])
            rng.shuffle(keys)
            total = sum(len(groups[k]) for k in keys)
            n_test, n_val = TEST_FRAC * total, VAL_FRAC * total
            seen = 0
            for k in keys:
                if seen < n_test:
                    s = "test"
                elif seen < n_test + n_val:
                    s = "val"
                else:
                    s = "train"
                assign[k] = s
                seen += len(groups[k])
    for key, members in groups.items():
        for m in members:
            m["split"] = assign[key]
            m["group"] = f"{key[0]}_{key[1]}"


def cross_split_dups(recs, max_dist=4):
    """Count image pairs in different splits whose dHash differs by <= max_dist bits."""
    bands = defaultdict(list)
    for i, r in enumerate(recs):
        h = r["dhash"]
        for b in range(8):  # 8 bands of 8 bits; a pair with <=4 diff bits shares at least 4 bands
            bands[(b, (h >> (8 * b)) & 0xFF)].append(i)
    pairs = set()
    for idx in bands.values():
        if len(idx) > 400:  # very common band value (flat images); skip to stay fast
            continue
        for a in range(len(idx)):
            for b in range(a + 1, len(idx)):
                i, j = idx[a], idx[b]
                if recs[i]["split"] != recs[j]["split"]:
                    if bin(recs[i]["dhash"] ^ recs[j]["dhash"]).count("1") <= max_dist:
                        pairs.add((min(i, j), max(i, j)))
    kinds = Counter(tuple(sorted((recs[i]["split"], recs[j]["split"]))) for i, j in pairs)
    return len(pairs), {f"{a}-{b}": n for (a, b), n in kinds.items()}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--stats-dir", default=None, help="where stats.json and manifest.csv go (default: --out)")
    ap.add_argument("--workers", type=int, default=4)
    a = ap.parse_args()
    src, out = Path(a.src), Path(a.out)
    stats_dir = Path(a.stats_dir or a.out)
    stats_dir.mkdir(parents=True, exist_ok=True)

    items, missing_img = find_items(src)
    print(f"labeled train xml with image: {len(items)}, xml without image: {missing_img}")

    # test folders: do they have labels?
    test_dirs = sorted({p for p in src.rglob("test") if p.is_dir()})
    test_info = {}
    for t in test_dirs:
        n_img = sum(1 for _ in (t / "images").glob("*")) if (t / "images").exists() else 0
        n_xml = sum(1 for _ in t.rglob("*.xml"))
        test_info[str(t.relative_to(src))] = {"images": n_img, "xml": n_xml}
    print("test folders:", json.dumps(test_info, indent=1))

    with Pool(a.workers) as pool:
        recs = pool.map(inspect_one, items, chunksize=64)

    make_split(recs)

    for s in ("train", "val", "test"):
        (out / "images" / s).mkdir(parents=True, exist_ok=True)
        (out / "labels" / s).mkdir(parents=True, exist_ok=True)
    with Pool(a.workers) as pool:
        pool.map(write_one, [(r, str(out)) for r in recs], chunksize=64)

    # data.yaml (path is rewritten by the training script to wherever the data lands)
    (out / "data.yaml").write_text(
        f"path: {out}\ntrain: images/train\nval: images/val\ntest: images/test\n"
        f"names:\n" + "".join(f"  {i}: {n}\n" for i, n in enumerate(CLASS_NAMES))
    )

    # stats
    st = {"n_labeled_images": len(recs), "xml_without_image": missing_img, "test_folders": test_info,
          "seed": SEED, "block": BLOCK, "max_side": MAX_SIDE, "dark_mean_threshold": DARK_MEAN}
    per_country = defaultdict(lambda: {"images": 0, "sizes": Counter(), "boxes": Counter(),
                                       "images_with_pothole": 0, "empty_images": 0, "dark_images": 0,
                                       "splits": Counter()})
    other = Counter()
    flags = Counter()
    per_split = defaultdict(lambda: {"images": 0, "boxes": Counter(), "images_with_pothole": 0, "empty_images": 0})
    for r in recs:
        c = per_country[r["country"]]
        c["images"] += 1
        c["sizes"][f"{r['w']}x{r['h']}"] += 1
        c["boxes"].update(r["counts"])
        c["images_with_pothole"] += int(r["counts"].get("D40", 0) > 0)
        c["empty_images"] += int(not r["lines"])
        c["dark_images"] += int(r["brightness"] < DARK_MEAN)
        c["splits"][r["split"]] += 1
        other.update(r["other"])
        flags.update(r["flags"])
        p = per_split[r["split"]]
        p["images"] += 1
        p["boxes"].update(r["counts"])
        p["images_with_pothole"] += int(r["counts"].get("D40", 0) > 0)
        p["empty_images"] += int(not r["lines"])
    st["per_country"] = {k: {kk: (dict(vv.most_common(5)) if kk == "sizes" else dict(vv) if isinstance(vv, Counter) else vv)
                             for kk, vv in v.items()} for k, v in sorted(per_country.items())}
    st["per_split"] = {k: {kk: (dict(vv) if isinstance(vv, Counter) else vv) for kk, vv in v.items()}
                       for k, v in per_split.items()}
    tot = Counter()
    for r in recs:
        tot.update(r["counts"])
    st["total_boxes"] = dict(tot)
    st["images_with_pothole"] = sum(int(r["counts"].get("D40", 0) > 0) for r in recs)
    st["other_labels_dropped"] = dict(other.most_common())
    st["box_flags"] = dict(flags)
    bright = sorted(r["brightness"] for r in recs)
    st["brightness_percentiles"] = {p: bright[min(len(bright) - 1, int(p / 100 * len(bright)))] for p in (1, 5, 10, 50, 90)}
    n_pairs, kinds = cross_split_dups(recs)
    st["near_duplicate_pairs_across_splits"] = {"total": n_pairs, "by_split_pair": kinds, "max_hamming": 4}

    (stats_dir / "stats.json").write_text(json.dumps(st, indent=1))
    with open(stats_dir / "manifest.csv", "w", newline="") as f:
        wr = csv.writer(f)
        wr.writerow(["stem", "country", "split", "group", "w", "h", "brightness", *CLASSES, "other"])
        for r in sorted(recs, key=lambda r: r["stem"]):
            wr.writerow([r["stem"], r["country"], r["split"], r["group"], r["w"], r["h"], r["brightness"],
                         *[r["counts"].get(c, 0) for c in CLASSES], sum(r["other"].values())])
    print(json.dumps({k: v for k, v in st.items() if k != "per_country"}, indent=1))
    print(json.dumps(st["per_country"], indent=1))


if __name__ == "__main__":
    main()
