"""Prepare RDD2022 for YOLO training.

What it does (all from the real files, nothing assumed):
1. Finds every labeled training image (Pascal VOC XML) in the RDD2022 folder,
   optionally only for some countries (--countries India,United_States).
2. Records per country: image count, image sizes, label counts, other labels,
   whether test labels exist, and a brightness estimate (to spot night images).
3. Converts boxes to YOLO format with a fixed class order (CLASS_SETS). "rdd4": 0 D00, 1 D10, 2 D20, 3 D40;
   "rdd5" adds 4 D50 (manhole cover). Ids 0-3 never change. Other labels are dropped and counted.
   Training kernels switch a built dataset to a smaller set with use_class_set ("rdd4", or "pothole" =
   potholes only, as class 0).
4. Checks for bad boxes (outside the image, tiny, zero size), XML sizes that do
   not match the image, and EXIF orientation tags, and reports them.
5. Makes a grouped, stratified train / val / test split (seed fixed).
   Group = country + block of BLOCK consecutive image ids, so frames that sit
   next to each other (often the same road) stay in the same split.
   Groups that share a CONFIRMED duplicate are merged into one split unit, so
   copies never cross splits. A duplicate is a pair in the same country with
   dHash distance <= --dup-dist (candidate) AND 64x64 grayscale thumbnails whose
   mean abs diff is < --dup-mad (confirmation). The dHash alone is too loose.
   Stratified by country, balancing both image count and pothole-image count. Each
   country is split on its own (own random generator), so adding or removing other
   countries does not change a country's split.
6. Writes images (only images wider than MAX_SIDE are resized; others are
   copied as is, except EXIF-rotated images, which are re-encoded without the
   tag), YOLO labels, data.yaml, manifest.csv and stats.json.
7. Counts near-duplicate images across splits with a 64-bit dHash (exact
   all-pairs check, so nothing is skipped).

Used by the data_v1 Kaggle job (ml/kaggle/data_v1/body.py). Locally:
  python prepare_rdd2022.py --src <RDD2022_all_countries> --out <dir> --countries India,United_States --classes rdd5
The split rule of the original b0 baseline (no duplicate merging) was removed on 18 Nov 2026; b0's split is
recorded in ml/reports/b0_yolo11s_640/ and git commit c4bfda1.
"""

import argparse
import csv
import json
import random
import re
import shutil
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from multiprocessing import Pool
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps

# Class sets. Ids are the list index, so only append; never reorder.
CLASS_SETS = {
    "rdd4": [("D00", "Longitudinal crack"), ("D10", "Transverse crack"),
             ("D20", "Alligator crack"), ("D40", "Pothole")],
    "rdd5": [("D00", "Longitudinal crack"), ("D10", "Transverse crack"),
             ("D20", "Alligator crack"), ("D40", "Pothole"), ("D50", "Manhole cover")],
    # Training-only subset (not a prefix of rdd5): pothole becomes class 0. Used by use_class_set.
    "pothole": [("D40", "Pothole")],
}
POTHOLE = "D40"

SEED = 42
BLOCK = 30          # consecutive image ids per group
VAL_FRAC = 0.10
TEST_FRAC = 0.10
MAX_SIDE = 1280     # keep enough pixels for imgsz 1024 tests
TINY_FRAC = 0.0001  # box area below 0.01% of the image counts as tiny
DARK_MEAN = 60      # mean grey level below this counts as "dark" (night or very low light)
DUP_DIST = 4        # dHash Hamming distance that makes a pair a duplicate CANDIDATE
DUP_MAD = 5.0       # candidate is confirmed if the mean abs diff of the 64x64 grayscale thumbnails is below this
LOOKALIKE_MAD = 8.0  # reported only: same-country pairs below this still look similar (same road, other spot)
EXIF_ORIENTATION = 0x0112
SWAPS_SIDES = (5, 6, 7, 8)  # EXIF orientations that rotate by 90 or 270 degrees


def country_of(stem):
    return stem.rsplit("_", 1)[0]


def id_of(stem):
    m = re.search(r"(\d+)$", stem)
    return int(m.group(1)) if m else 0


def has_pothole(rec):
    return rec["counts"].get(POTHOLE, 0) > 0


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
    """Return (objects, (width, height) from <size> or None)."""
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
    size = None
    try:
        sw, sh = int(float(root.findtext("size/width"))), int(float(root.findtext("size/height")))
        if sw > 0 and sh > 0:
            size = (sw, sh)
    except (TypeError, ValueError):
        pass
    return objs, size


def dhash(img):
    g = img.convert("L").resize((9, 8), Image.BILINEAR)
    px = list(g.getdata())
    bits = 0
    for r in range(8):
        for c in range(8):
            bits = (bits << 1) | (px[r * 9 + c] > px[r * 9 + c + 1])
    return bits


def inspect_one(args):
    """Open one image, read its XML, build YOLO lines and checks.

    args = (xml_path, img_path, class_codes). class_codes is passed in (not a global) so it also works with
    spawn-based multiprocessing on Windows.
    """
    xml, img_path, class_codes = args
    class_id = {c: i for i, c in enumerate(class_codes)}
    stem = Path(img_path).stem
    out = {"stem": stem, "img": str(img_path), "country": country_of(stem)}
    flags = Counter()
    objs, xml_size = parse_xml(xml)
    with Image.open(img_path) as im:
        raw_w, raw_h = im.size
        try:
            orient = int(im.getexif().get(EXIF_ORIENTATION, 1) or 1)
        except Exception:
            orient = 1
        small = im.convert("L").resize((64, 64), Image.BILINEAR)
        out["brightness"] = round(sum(small.getdata()) / (64 * 64), 1)
        out["dhash"] = dhash(im)
        out["thumb"] = small.tobytes()  # 64x64 grayscale, used to confirm duplicate candidates
    # Which pixel frame are the boxes in? Image loaders (PIL exif_transpose,
    # OpenCV imread, Ultralytics) apply the EXIF rotation, but the boxes may
    # have been drawn on the raw pixels. The XML <size> tells us which.
    rot_size = (raw_h, raw_w) if orient in SWAPS_SIDES else (raw_w, raw_h)
    transpose = orient in SWAPS_SIDES and xml_size == rot_size and rot_size != (raw_w, raw_h)
    w, h = rot_size if transpose else (raw_w, raw_h)
    out["w"], out["h"] = w, h
    out["exif_orient"] = orient
    out["transpose"] = transpose
    if orient != 1:
        flags["exif_orientation_tag"] += 1
        flags["exif_boxes_in_rotated_frame" if transpose else "exif_boxes_in_raw_frame"] += 1
    if xml_size is not None and xml_size != (w, h):
        flags["xml_size_mismatch"] += 1
    counts = Counter()
    other = Counter()
    lines = []
    for name, box in objs:
        if box is None:
            flags["no_box"] += 1
            continue
        if name not in class_id:
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
        counts[name] += 1
        lines.append(f"{class_id[name]} {(x1 + x2) / 2 / w:.6f} {(y1 + y2) / 2 / h:.6f} {bw / w:.6f} {bh / h:.6f}")
    out["counts"] = dict(counts)
    out["other"] = dict(other)
    out["flags"] = dict(flags)
    out["lines"] = lines
    return out


def write_one(args):
    """Write one image and its label file.

    Images with an EXIF orientation tag are always re-encoded without the tag,
    in the frame the boxes use, so every loader sees the same pixels as the labels.
    """
    rec, out_dir = args
    split = rec["split"]
    img_dst = Path(out_dir) / "images" / split / (rec["stem"] + ".jpg")
    lbl_dst = Path(out_dir) / "labels" / split / (rec["stem"] + ".txt")
    src = Path(rec["img"])
    orient = rec.get("exif_orient", 1)
    if max(rec["w"], rec["h"]) > MAX_SIDE or orient != 1:
        with Image.open(src) as im:
            if rec.get("transpose"):
                im = ImageOps.exif_transpose(im)
            im = im.convert("RGB")
            if max(im.size) > MAX_SIDE:
                s = MAX_SIDE / max(im.size)
                im = im.resize((round(im.size[0] * s), round(im.size[1] * s)), Image.LANCZOS)
            im.save(img_dst, quality=95)  # no exif= argument, so the tag is not written
    elif src.suffix.lower() in (".jpg", ".jpeg"):
        shutil.copyfile(src, img_dst)
    else:
        with Image.open(src) as im:
            im.convert("RGB").save(img_dst, quality=95)
    lbl_dst.write_text("\n".join(rec["lines"]) + ("\n" if rec["lines"] else ""))


def popcount64(x):
    if hasattr(np, "bitwise_count"):
        return np.bitwise_count(x)
    table = np.array([bin(i).count("1") for i in range(256)], dtype=np.uint8)
    return table[x.view(np.uint8).reshape(*x.shape, 8)].sum(axis=-1)


def near_dup_pairs(hashes, max_dist, chunk=256):
    """Exact list of index pairs (i < j) whose 64-bit hashes differ by <= max_dist bits."""
    h = np.array(hashes, dtype=np.uint64)
    n = len(h)
    pairs = []
    for s in range(0, n, chunk):
        e = min(n, s + chunk)
        d = popcount64(h[s:e, None] ^ h[None, :])
        ii, jj = np.nonzero(d <= max_dist)
        ii = ii + s
        keep = jj > ii
        pairs.extend(zip(ii[keep].tolist(), jj[keep].tolist()))
    return pairs


def pair_mad(recs, pairs):
    """Mean absolute pixel difference (0..255) of the 64x64 grayscale thumbnails, per pair."""
    if not pairs:
        return np.zeros(0, dtype=np.float32)
    cache = {}

    def th(i):
        if i not in cache:
            cache[i] = np.frombuffer(recs[i]["thumb"], dtype=np.uint8).astype(np.float32)
        return cache[i]

    return np.array([np.abs(th(i) - th(j)).mean() for i, j in pairs], dtype=np.float32)


def confirm_pairs(recs, pairs, max_mad):
    """Keep dHash candidates that are in the same country AND look alike at pixel level.

    A dHash on an 8x9 thumbnail alone is far too loose for road photos: on RDD2022 only about 1 in 25
    candidates is a real repeat (14 Nov 2026 near-duplicate analysis, ml/kaggle/dup_analysis).
    """
    same = [(i, j) for i, j in pairs if recs[i]["country"] == recs[j]["country"]]
    mad = pair_mad(recs, same)
    return [p for p, m in zip(same, mad) if m < max_mad]


class _UnionFind:
    def __init__(self):
        self.parent = {}

    def find(self, x):
        self.parent.setdefault(x, x)
        root = x
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[x] != root:
            self.parent[x], x = root, self.parent[x]
        return root

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            # Smaller key becomes the root, so the result does not depend on pair order.
            if rb < ra:
                ra, rb = rb, ra
            self.parent[rb] = ra


def make_split(recs, dup_pairs):
    """Assign rec["split"], rec["group"], rec["unit"]. dup_pairs: index pairs of duplicates to keep together.

    Merged units vary a lot in size (a long video sequence can be thousands of images), so a yes/no "has
    pothole" stratum is not enough: per country, units are placed largest first into the split that keeps
    both the image count and the pothole-image count closest to 80/10/10.
    """
    gkey = [(r["country"], id_of(r["stem"]) // BLOCK) for r in recs]
    groups = defaultdict(list)
    for k, r in zip(gkey, recs):
        groups[k].append(r)

    # Merge block groups that share a near-duplicate pair into one split unit.
    uf = _UnionFind()
    for key in groups:
        uf.find(key)
    for i, j in dup_pairs:
        uf.union(gkey[i], gkey[j])
    units = defaultdict(list)  # unit key (smallest group key) -> group keys
    for key in groups:
        units[uf.find(key)].append(key)

    by_country = defaultdict(list)
    unit_size = {}
    unit_pot = {}  # images with at least one pothole box
    for ukey, gkeys in units.items():
        members = [m for k in gkeys for m in groups[k]]
        unit_size[ukey] = len(members)
        unit_pot[ukey] = sum(1 for m in members if has_pothole(m))
        # A unit can span countries (cross-country duplicates); file it under the
        # country with most images, ties broken by name.
        cc = Counter(m["country"] for m in members)
        by_country[sorted(cc.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]].append(ukey)

    assign = {}
    fracs = {"train": 1 - VAL_FRAC - TEST_FRAC, "val": VAL_FRAC, "test": TEST_FRAC}
    for country in sorted(by_country):
        keys = sorted(by_country[country])
        # One generator per country, so a country's split does not change when other countries are added to or
        # removed from the run.
        random.Random(f"{SEED}-{country}").shuffle(keys)  # random order among units of equal size
        keys.sort(key=lambda k: -unit_size[k])  # stable sort: largest first
        n_img = sum(unit_size[k] for k in keys)
        n_pot = sum(unit_pot[k] for k in keys)
        img = {s: 0 for s in fracs}
        pot = {s: 0 for s in fracs}

        def cost(after_img, after_pot):
            c = sum(((after_img[s] - f * n_img) / max(n_img, 1)) ** 2 for s, f in fracs.items())
            return c + sum(((after_pot[s] - f * n_pot) / max(n_pot, 1)) ** 2 for s, f in fracs.items())

        for k in keys:
            best = None
            for order, s in enumerate(("train", "val", "test")):
                ai, ap = dict(img), dict(pot)
                ai[s] += unit_size[k]
                ap[s] += unit_pot[k]
                cand = (cost(ai, ap), order, s)
                best = cand if best is None or cand < best else best
            s = best[2]
            img[s] += unit_size[k]
            pot[s] += unit_pot[k]
            assign[k] = s
        # Repair: a country with few, large units can leave val or test
        # empty. Move the smallest train unit (up to 3x the target) there,
        # preferring one with potholes.
        for s in ("test", "val"):
            if img[s] == 0:
                cands = [k for k in keys if assign[k] == "train" and unit_size[k] <= 3 * fracs[s] * n_img]
                if cands:
                    k = min(cands, key=lambda k: (unit_pot[k] == 0, unit_size[k], k))
                    assign[k] = s
                    img["train"] -= unit_size[k]
                    img[s] += unit_size[k]
    for key, members in groups.items():
        ukey = uf.find(key)
        for m in members:
            m["split"] = assign[ukey]
            m["group"] = f"{key[0]}_{key[1]}"
            m["unit"] = f"{ukey[0]}_{ukey[1]}"
    sizes = sorted(unit_size.values(), reverse=True)
    return {"block_groups": len(groups), "split_units": len(units),
            "merged_units": sum(1 for g in units.values() if len(g) > 1),
            "largest_unit_images": sizes[0] if sizes else 0,
            "largest_units_images": sizes[:10]}


def cross_split_dups(recs, pairs):
    """Count the given image pairs that sit in different splits, by split pair."""
    cross = [(i, j) for i, j in pairs if recs[i]["split"] != recs[j]["split"]]
    kinds = Counter(tuple(sorted((recs[i]["split"], recs[j]["split"]))) for i, j in cross)
    return len(cross), {f"{a}-{b}": n for (a, b), n in kinds.items()}


def use_class_set(data_root, class_set):
    """Switch a prepared dataset to a smaller class set, in place.

    For training kernels: data_v1 ships rdd5 labels; rdd4 = the same files with
    class-4 lines removed (ids 0-3 are the same in both sets). Edits the label
    files under data_root and rewrites data_root/data.yaml. No symlinks: Ultralytics
    resolves dataset paths, so a symlinked images dir would make it read the
    labels next to the symlink target instead.
    Returns (data.yaml path, number of box lines removed).
    """
    data_root = Path(data_root)
    codes = CLASS_SETS[class_set]
    # old class id (rdd5 order) -> new id in this set; classes outside the set are dropped.
    # rdd4 maps ids 0-3 to themselves, so its behaviour is unchanged; "pothole" makes pothole class 0.
    full_ids = {c: i for i, (c, _) in enumerate(CLASS_SETS["rdd5"])}
    remap = {full_ids[c]: i for i, (c, _) in enumerate(codes)}
    removed = 0
    for p in sorted((data_root / "labels").glob("*/*.txt")):
        lines = [ln for ln in p.read_text().splitlines() if ln.strip()]
        keep = []
        for ln in lines:
            parts = ln.split()
            if int(parts[0]) in remap:
                keep.append(" ".join([str(remap[int(parts[0])])] + parts[1:]))
        removed += len(lines) - len(keep)
        if keep != lines:
            p.write_text("\n".join(keep) + ("\n" if keep else ""))
    y = data_root / "data.yaml"
    y.write_text(f"path: {data_root}\ntrain: images/train\nval: images/val\ntest: images/test\n"
                 "names:\n" + "".join(f"  {i}: {name}\n" for i, (_, name) in enumerate(codes)))
    return y, removed


def build_stats(recs, cand_pairs, dup_dist, dup_mad):
    """Everything stats.json reports about the built dataset (beyond the run settings)."""
    per_country = defaultdict(lambda: {"images": 0, "sizes": Counter(), "boxes": Counter(),
                                       "images_with_pothole": 0, "empty_images": 0, "dark_images": 0,
                                       "splits": Counter()})
    per_split = defaultdict(lambda: {"images": 0, "boxes": Counter(), "images_with_pothole": 0, "empty_images": 0})
    other, flags, total = Counter(), Counter(), Counter()
    for r in recs:
        for d in (per_country[r["country"]], per_split[r["split"]]):
            d["images"] += 1
            d["boxes"].update(r["counts"])
            d["images_with_pothole"] += int(has_pothole(r))
            d["empty_images"] += int(not r["lines"])
        c = per_country[r["country"]]
        c["sizes"][f"{r['w']}x{r['h']}"] += 1
        c["dark_images"] += int(r["brightness"] < DARK_MEAN)
        c["splits"][r["split"]] += 1
        other.update(r["other"])
        flags.update(r["flags"])
        total.update(r["counts"])
    st = {"per_country": {k: {kk: (dict(vv.most_common(5)) if kk == "sizes" else dict(vv) if isinstance(vv, Counter) else vv)
                              for kk, vv in v.items()} for k, v in sorted(per_country.items())},
          "per_split": {k: {kk: (dict(vv) if isinstance(vv, Counter) else vv) for kk, vv in v.items()}
                        for k, v in per_split.items()},
          "total_boxes": dict(total),
          "images_with_pothole": sum(int(has_pothole(r)) for r in recs),
          "other_labels_dropped": dict(other.most_common()),
          "box_flags": dict(flags),
          "exif_orientations": dict(Counter(str(r["exif_orient"]) for r in recs))}
    bright = sorted(r["brightness"] for r in recs)
    st["brightness_percentiles"] = {p: bright[min(len(bright) - 1, int(p / 100 * len(bright)))] for p in (1, 5, 10, 50, 90)}
    n_pairs, kinds = cross_split_dups(recs, cand_pairs)
    st["near_duplicate_pairs_total"] = len(cand_pairs)
    st["near_duplicate_pairs_across_splits"] = {"total": n_pairs, "by_split_pair": kinds, "max_hamming": dup_dist,
                                                "method": "exact all-pairs",
                                                "note": "raw dHash candidates; most are look-alike roads, not copies"}
    # Confirmed duplicates (same country, thumbnail MAD < dup_mad): these must never cross splits.
    confirmed = confirm_pairs(recs, cand_pairs, dup_mad)
    cn, ck = cross_split_dups(recs, confirmed)
    st["confirmed_duplicate_pairs_total"] = len(confirmed)
    st["confirmed_duplicate_pairs_across_splits"] = {"total": cn, "by_split_pair": ck,
                                                     "rule": f"dHash<={dup_dist}, same country, MAD<{dup_mad}"}
    lookalike = confirm_pairs(recs, cand_pairs, LOOKALIKE_MAD)
    ln, lk = cross_split_dups(recs, lookalike)
    st["lookalike_pairs_total"] = len(lookalike)
    st["lookalike_pairs_across_splits"] = {"total": ln, "by_split_pair": lk, "rule": f"same country, MAD<{LOOKALIKE_MAD}",
                                           "note": "informational: same road at another spot, not repeated frames"}
    # Split balance per country (used by the data kernel as a hard check).
    bal = {}
    for c, v in st["per_country"].items():
        n = max(v["images"], 1)
        pot = Counter(r["split"] for r in recs if r["country"] == c and has_pothole(r))
        bal[c] = {"val_share": round(v["splits"].get("val", 0) / n, 4), "test_share": round(v["splits"].get("test", 0) / n, 4),
                  "pothole_images": {k: pot.get(k, 0) for k in ("train", "val", "test")}}
    st["split_balance"] = bal
    return st


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--stats-dir", default=None, help="where stats.json and manifest.csv go (default: --out)")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--classes", choices=["rdd4", "rdd5"], default="rdd4",
                    help="rdd4 = 4 damage classes; rdd5 = rdd4 + D50 manhole cover as class 4")
    ap.add_argument("--countries", default="all",
                    help="comma-separated country names to keep, e.g. India,United_States; default all")
    ap.add_argument("--dup-mad", type=float, default=DUP_MAD,
                    help="confirm dHash candidates by thumbnail mean abs diff below this (same country only)")
    ap.add_argument("--dup-dist", type=int, default=DUP_DIST,
                    help="dHash distance for duplicate candidates (merged into one split unit once confirmed)")
    a = ap.parse_args()
    src, out = Path(a.src), Path(a.out)
    stats_dir = Path(a.stats_dir or a.out)
    stats_dir.mkdir(parents=True, exist_ok=True)
    class_codes = [c for c, _ in CLASS_SETS[a.classes]]
    class_names = [n for _, n in CLASS_SETS[a.classes]]

    items, missing_img = find_items(src)
    print(f"labeled train xml with image: {len(items)}, xml without image: {missing_img}")
    wanted = None
    if a.countries.strip().lower() != "all":
        wanted = [c.strip() for c in a.countries.split(",") if c.strip()]
        have = Counter(country_of(Path(i).stem) for _, i in items)
        unknown = [c for c in wanted if have[c] == 0]
        if unknown:
            raise SystemExit(f"no labeled images for {unknown}; available: {sorted(have)}")
        items = [(x, i) for x, i in items if country_of(Path(i).stem) in wanted]
        print(f"keeping countries {wanted}: {len(items)} images")

    # test folders: do they have labels?
    test_info = {}
    for t in sorted({p for p in src.rglob("test") if p.is_dir()}):
        n_img = sum(1 for _ in (t / "images").glob("*")) if (t / "images").exists() else 0
        test_info[str(t.relative_to(src))] = {"images": n_img, "xml": sum(1 for _ in t.rglob("*.xml"))}
    print("test folders:", json.dumps(test_info, indent=1))

    with Pool(a.workers) as pool:
        recs = pool.map(inspect_one, [(x, i, class_codes) for x, i in items], chunksize=64)

    cand_pairs = near_dup_pairs([r["dhash"] for r in recs], a.dup_dist)
    merge_pairs = confirm_pairs(recs, cand_pairs, a.dup_mad)
    print(f"duplicate candidates (dHash <= {a.dup_dist}): {len(cand_pairs)}, merged after confirmation: {len(merge_pairs)}")
    split_info = make_split(recs, merge_pairs)
    print("split units:", json.dumps(split_info))

    for s in ("train", "val", "test"):
        (out / "images" / s).mkdir(parents=True, exist_ok=True)
        (out / "labels" / s).mkdir(parents=True, exist_ok=True)
    with Pool(a.workers) as pool:
        pool.map(write_one, [(r, str(out)) for r in recs], chunksize=64)

    # data.yaml (path is rewritten by the training script to wherever the data lands)
    (out / "data.yaml").write_text(
        f"path: {out}\ntrain: images/train\nval: images/val\ntest: images/test\n"
        f"names:\n" + "".join(f"  {i}: {n}\n" for i, n in enumerate(class_names))
    )

    st = {"n_labeled_images": len(recs), "xml_without_image": missing_img, "test_folders": test_info,
          "seed": SEED, "block": BLOCK, "max_side": MAX_SIDE, "dark_mean_threshold": DARK_MEAN,
          "countries": wanted or "all", "class_set": a.classes, "classes": dict(zip(class_codes, class_names)),
          "dup_merge_dist": a.dup_dist, "dup_merge_mad": a.dup_mad, "split_rule": "balanced",
          "split_units": split_info, **build_stats(recs, cand_pairs, a.dup_dist, a.dup_mad)}
    (stats_dir / "stats.json").write_text(json.dumps(st, indent=1))
    with open(stats_dir / "manifest.csv", "w", newline="") as f:
        wr = csv.writer(f)
        wr.writerow(["stem", "country", "split", "group", "unit", "w", "h", "brightness", "exif_orient",
                     *class_codes, "other"])
        for r in sorted(recs, key=lambda r: r["stem"]):
            wr.writerow([r["stem"], r["country"], r["split"], r["group"], r["unit"], r["w"], r["h"], r["brightness"],
                         r["exif_orient"], *[r["counts"].get(c, 0) for c in class_codes], sum(r["other"].values())])
    print(json.dumps({k: v for k, v in st.items() if k != "per_country"}, indent=1))
    print(json.dumps(st["per_country"], indent=1))


if __name__ == "__main__":
    main()
