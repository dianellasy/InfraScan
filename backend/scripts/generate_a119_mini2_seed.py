#!/usr/bin/env python3
"""Generate a deterministic synthetic A119 Mini 2-like MP4 seed clip.

Output is labeled `synthetic-a119-mini2-like`: it is NOT camera footage. The
video is a generated road-like pattern encoded as 1920x1080 30 fps H.264
(yuv420p, High profile), with optional silent AAC audio. Novatek freeGPS
blocks (VIOFO A119 Mini 2 "Type 3"/ExifTool branch 1C layout) are interleaved
inside `mdat` about once per second of video, and a `moov/gps ` index points
at them, mirroring how the camera stores telemetry.

Record packing here is written independently of
`inference.telemetry.a119`; the committed golden fixture
`tests/fixtures/a119_mini2_type3_golden.bin` and the optional ExifTool `-ee`
check (`--validate-exiftool`) keep the two from agreeing on a shared mistake.

Examples (from `backend/`):

    python scripts/generate_a119_mini2_seed.py --output-dir /tmp/a119-seed
    python scripts/generate_a119_mini2_seed.py --scenario cold-start --audio \
        --output-dir /tmp/a119-seed --validate-exiftool

Known limitations versus real A119 Mini 2 files: encoder settings, brands,
time base, GPS block size/padding, the `gps ` header word, and unknown
trailing fields (for example accelerometer data) are approximations.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import shutil
import struct
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path

LABEL = "synthetic-a119-mini2-like"
SCENARIOS = (
    "standard",
    "cold-start",
    "duplicate",
    "short-gap",
    "long-gap",
    "corrupt-block",
    "broken-index",
    "no-gps",
    "southeast",
    "north-crossing",
    "stationary",
)
GPS_BLOCK_SIZE = 0x8000
KNOTS_PER_KMH = 1 / 1.852

# Stanton/Anaheim seed area: Katella Ave at Harbor Blvd, driving north.
NORTH_WEST_START = (33.80310, -117.91460)
# Southern/eastern hemisphere check: central Sydney, driving north.
SOUTH_EAST_START = (-33.86880, 151.20930)
EARTH_RADIUS_M = 6_371_008.8


@dataclass
class Record:
    slot: int
    status: str  # "A" active, "V" void
    camera_time: str  # naive camera clock, ISO 8601
    latitude: float | None
    longitude: float | None
    speed_kmh: float | None
    course_deg: float | None
    note: str = ""
    corrupt: bool = False
    duplicate_of: int | None = None


# --- Telemetry generation ---


def _advance(latitude: float, longitude: float, course_deg: float, meters: float):
    heading = math.radians(course_deg)
    north, east = meters * math.cos(heading), meters * math.sin(heading)
    latitude += math.degrees(north / EARTH_RADIUS_M)
    longitude += math.degrees(east / (EARTH_RADIUS_M * math.cos(math.radians(latitude))))
    return latitude, longitude


def build_records(scenario: str, seconds: int, start: datetime, offset_minutes: int) -> list[Record]:
    if scenario == "no-gps":
        return []
    latitude, longitude = SOUTH_EAST_START if scenario == "southeast" else NORTH_WEST_START
    # A parked camera keeps one position and course, so a fixed box projects
    # to the same ground point in every frame.
    speed_kmh = 0.0 if scenario == "stationary" else 40.0
    records: list[Record] = []
    for slot in range(seconds):
        course = 0.8
        if scenario == "north-crossing":
            course = (356.0 + slot * 1.5) % 360
        camera_time = start + timedelta(seconds=slot)
        record = Record(
            slot, "A", camera_time.isoformat(), latitude, longitude, speed_kmh, course
        )
        if scenario == "cold-start" and slot < 3:
            # Pre-lock records carry the RTC in UTC, not the local wall time.
            rtc = camera_time - timedelta(minutes=offset_minutes)
            record = Record(slot, "V", rtc.isoformat(), None, None, None, None, "pre-lock RTC (UTC)")
        elif scenario == "short-gap" and slot in (6, 7):
            record = Record(slot, "V", camera_time.isoformat(), None, None, None, None, "lost fix")
        elif scenario == "long-gap" and 3 <= slot <= 10:
            record = Record(slot, "V", camera_time.isoformat(), None, None, None, None, "tunnel")
        elif scenario == "corrupt-block" and slot == 4:
            record.corrupt = True
            record.note = "invalid month 13; must be rejected"
        elif scenario == "duplicate" and slot == 5:
            previous = records[-1]
            record = Record(
                slot, "A", previous.camera_time, previous.latitude, previous.longitude,
                previous.speed_kmh, previous.course_deg, "byte-identical stale repeat",
                duplicate_of=slot - 1,
            )
        records.append(record)
        latitude, longitude = _advance(latitude, longitude, course, speed_kmh / 3.6)
    return records


def _ddmm(value: float) -> float:
    magnitude = abs(value)
    degrees = int(magnitude)
    return degrees * 100 + (magnitude - degrees) * 60


def pack_block(record: Record) -> bytes:
    """One `free` box holding an A119 Mini 2 freeGPS record (little endian)."""
    when = datetime.fromisoformat(record.camera_time)
    month = 13 if record.corrupt else when.month
    header = struct.pack(">I4s4s", GPS_BLOCK_SIZE, b"free", b"GPS ")
    body = struct.pack(
        "<7I", 0x2C, when.hour, when.minute, when.second, when.year - 2000, month, when.day
    )
    if record.status == "A":
        flags = b"A" + (b"N" if record.latitude >= 0 else b"S") + (
            b"E" if record.longitude >= 0 else b"W"
        ) + b"\0"
        values = struct.pack(
            "<4f",
            _ddmm(record.latitude),
            _ddmm(record.longitude),
            record.speed_kmh * KNOTS_PER_KMH,
            record.course_deg,
        )
    else:
        flags = b"V00\0"
        values = bytes(16)
    block = header + body + flags + values
    return block + bytes(GPS_BLOCK_SIZE - len(block))


def written_values(record: Record) -> dict[str, float | None]:
    """Decimal values stored in the float32 fields (corrupt blocks included)."""
    if record.status != "A":
        return {"latitude": None, "longitude": None, "speed_kmh": None, "course_deg": None}

    def f32(value: float) -> float:
        return struct.unpack("<f", struct.pack("<f", value))[0]

    def decimal(ddmm: float, negative: bool) -> float:
        degrees = int(ddmm // 100)
        value = degrees + (ddmm - degrees * 100) / 60
        return -value if negative else value

    return {
        "latitude": decimal(f32(_ddmm(record.latitude)), record.latitude < 0),
        "longitude": decimal(f32(_ddmm(record.longitude)), record.longitude < 0),
        "speed_kmh": f32(record.speed_kmh * KNOTS_PER_KMH) * 1.852,
        "course_deg": f32(record.course_deg),
    }


# --- MP4 rewriting ---

_CONTAINERS = {b"moov", b"trak", b"mdia", b"minf", b"stbl"}


def _boxes(data: bytes | bytearray, start: int, end: int):
    position = start
    while position + 8 <= end:
        size, kind = struct.unpack_from(">I4s", data, position)
        header = 8
        if size == 1:
            (size,) = struct.unpack_from(">Q", data, position + 8)
            header = 16
        elif size == 0:
            size = end - position
        if size < header or position + size > end:
            raise ValueError(f"malformed box {kind!r} at {position}")
        yield kind, position, header, size
        position += size


def _chunk_offset_tables(data: bytes, start: int, end: int, found: list):
    for kind, position, header, size in _boxes(data, start, end):
        if kind in _CONTAINERS:
            _chunk_offset_tables(data, position + header, position + size, found)
        elif kind in (b"stco", b"co64"):
            (count,) = struct.unpack_from(">I", data, position + header + 4)
            found.append((kind, position + header + 8, count))
    return found


def interleave_gps(mp4: bytes, blocks: list[bytes], *, write_index: bool, break_index: bool) -> bytes:
    top = {kind: (position, header, size) for kind, position, header, size in _boxes(mp4, 0, len(mp4))}
    mdat_pos, mdat_header, mdat_size = top[b"mdat"]
    moov_pos, moov_header, moov_size = top[b"moov"]
    if moov_pos < mdat_pos:
        raise ValueError("expected moov after mdat (no faststart)")
    payload_start, payload_end = mdat_pos + mdat_header, mdat_pos + mdat_size

    tables = _chunk_offset_tables(mp4, moov_pos + moov_header, moov_pos + moov_size, [])
    chunk_offsets = sorted(
        {
            struct.unpack_from(">I" if kind == b"stco" else ">Q", mp4, base + i * (4 if kind == b"stco" else 8))[0]
            for kind, base, count in tables
            for i in range(count)
        }
    )
    # Block i goes before the first chunk at or after i/N of the media payload.
    points = []
    for i in range(len(blocks)):
        target = payload_start + (payload_end - payload_start) * i / max(1, len(blocks))
        points.append(next((c for c in chunk_offsets if c >= target), payload_end))

    def shifted(offset: int) -> int:
        return offset + GPS_BLOCK_SIZE * sum(point <= offset for point in points)

    out = bytearray()
    cursor = 0
    block_offsets = []
    for point, block in sorted(zip(points, blocks), key=lambda item: item[0]):
        out += mp4[cursor:point]
        block_offsets.append(len(out))
        out += block
        cursor = point
    out += mp4[cursor:]

    inserted = GPS_BLOCK_SIZE * len(blocks)
    if mdat_header == 8:
        struct.pack_into(">I", out, mdat_pos, mdat_size + inserted)
    else:
        struct.pack_into(">Q", out, mdat_pos + 8, mdat_size + inserted)
    new_moov_pos = moov_pos + inserted
    for kind, base, count in tables:
        width = 4 if kind == b"stco" else 8
        fmt = ">I" if kind == b"stco" else ">Q"
        for i in range(count):
            position = base + inserted + i * width
            (offset,) = struct.unpack_from(fmt, out, position)
            struct.pack_into(fmt, out, position, shifted(offset))

    if write_index and blocks:
        entries = [(offset, GPS_BLOCK_SIZE) for offset in block_offsets]
        if break_index:
            entries[len(entries) // 2] = (len(out) + 10 * GPS_BLOCK_SIZE, GPS_BLOCK_SIZE)
        payload = struct.pack(">II", 0, len(entries)) + b"".join(
            struct.pack(">II", offset, size) for offset, size in entries
        )
        gps_box = struct.pack(">I4s", 8 + len(payload), b"gps ") + payload
        # Before any trak, as Novatek writes it; ExifTool only reads it there.
        mvhd = next(
            (position + size
             for kind, position, _, size in _boxes(out, new_moov_pos + moov_header, new_moov_pos + moov_size)
             if kind == b"mvhd"),
            new_moov_pos + moov_header,
        )
        out[mvhd:mvhd] = gps_box
        struct.pack_into(">I", out, new_moov_pos, moov_size + len(gps_box))
    return bytes(out)


# --- Video encoding ---


def encode_video(path: Path, seconds: int, width: int, height: int, fps: int, audio: bool) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise SystemExit("ffmpeg is required to generate the seed clip")
    graph = (
        f"[0:v]drawbox=x=0:y=0:w=iw:h=ih*0.42:color=0x8fa9c4:t=fill,"
        f"drawbox=x=0:y=ih*0.42:w=iw:h=ih*0.58:color=0x4d4f52:t=fill,"
        f"drawbox=x=iw*0.1:y=ih*0.42:w=iw*0.012:h=ih*0.58:color=0xd8d8d0:t=fill,"
        f"drawbox=x=iw*0.888:y=ih*0.42:w=iw*0.012:h=ih*0.58:color=0xd8d8d0:t=fill,"
        f"noise=alls=10:allf=u:all_seed=7[road];"
        f"[1:v]format=yuva420p[dash];"
        f"[road][dash]overlay=x='(W-w)/2':y='H*0.45+mod(t*360,H*0.55)':eval=frame,"
        f"format=yuv420p[v]"
    )
    args = [
        ffmpeg, "-hide_banner", "-nostdin", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", f"color=c=0x55565a:s={width}x{height}:r={fps}:d={seconds}",
        "-f", "lavfi", "-i", f"color=c=0xf0f0e8:s={width // 80}x{height // 9}:r={fps}:d={seconds}",
    ]
    if audio:
        args += ["-f", "lavfi", "-i", f"anullsrc=r=48000:cl=mono:d={seconds}"]
    args += ["-filter_complex", graph, "-map", "[v]"]
    if audio:
        args += ["-map", "2:a", "-c:a", "aac", "-b:a", "32k"]
    args += [
        "-c:v", "libx264", "-preset", "veryfast", "-profile:v", "high", "-pix_fmt", "yuv420p",
        "-crf", "32", "-g", str(fps), "-bf", "2", "-threads", "1",
        "-r", str(fps), "-t", str(seconds),
        "-fflags", "+bitexact", "-flags:v", "+bitexact", "-flags:a", "+bitexact",
        "-map_metadata", "-1", "-f", "mp4", str(path),
    ]
    subprocess.run(args, check=True, timeout=600)


def ffmpeg_version() -> str:
    try:
        output = subprocess.run(
            ["ffmpeg", "-version"], capture_output=True, text=True, timeout=30, check=True
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    return output.splitlines()[0] if output else "unknown"


# --- ExifTool cross-check ---


def exiftool_fixes(path: Path) -> list[dict]:
    exiftool = shutil.which("exiftool")
    if exiftool is None:
        raise SystemExit("exiftool is not installed")
    output = subprocess.run(
        [exiftool, "-ee", "-n", "-j", "-G3", "-GPSDateTime", "-GPSLatitude", "-GPSLongitude",
         "-GPSSpeed", "-GPSTrack", str(path)],
        capture_output=True, text=True, timeout=120, check=True,
    ).stdout
    document = json.loads(output)[0]
    fixes: dict[str, dict] = {}
    for key, value in document.items():
        group, _, name = key.partition(":")
        if group.startswith("Doc"):
            fixes.setdefault(group, {})[name] = value
    return [fixes[group] for group in sorted(fixes, key=lambda g: int(g[3:]))]


def validate_with_exiftool(path: Path, manifest: dict) -> list[str]:
    """Compare ExifTool's independent decode with what was written.

    ExifTool decodes every active block through the `gps ` index, including
    stale repeats and blocks InfraScan rejects for other reasons (for example
    an impossible month), so those are expected here too.
    """
    if not manifest["gps_index"] or manifest["scenario"] == "broken-index":
        return ["ExifTool decodes A119 Mini 2 blocks only through a valid gps index"]
    expected = [r for r in manifest["records"] if r["status"] == "A"]
    found = exiftool_fixes(path)
    problems = []
    if len(found) != len(expected):
        problems.append(f"ExifTool decoded {len(found)} fixes, expected {len(expected)}")
    for fix, record in zip(found, expected):
        for name, key in (("GPSLatitude", "latitude"), ("GPSLongitude", "longitude")):
            want = record["written"][key]
            if abs(float(fix.get(name, "nan")) - want) > 1e-6:
                problems.append(f"slot {record['slot']}: {name} {fix.get(name)} != {want}")
    return problems


# --- CLI ---


def generate(args: argparse.Namespace) -> tuple[Path, Path, dict]:
    start = datetime.fromisoformat(args.start)
    if start.tzinfo is not None:
        raise SystemExit("--start is the camera's local wall time; omit the UTC offset")
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    name = f"{start:%Y%m%d%H%M%S}_{args.counter:06d}.MP4"
    target = output_dir / name

    records = build_records(args.scenario, args.seconds, start, args.camera_utc_offset_minutes)
    blocks = [pack_block(r) for r in records]
    with tempfile.TemporaryDirectory(prefix="a119-seed-") as tmp:
        raw = Path(tmp) / "raw.mp4"
        encode_video(raw, args.seconds, args.width, args.height, args.fps, args.audio)
        data = interleave_gps(
            raw.read_bytes(), blocks,
            write_index=args.scenario != "no-gps" and not args.no_index,
            break_index=args.scenario == "broken-index",
        )
    target.write_bytes(data)

    offset = timedelta(minutes=args.camera_utc_offset_minutes)
    active = [r for r in records if r.status == "A" and not r.corrupt and r.duplicate_of is None]
    manifest = {
        "label": LABEL,
        "generator": "backend/scripts/generate_a119_mini2_seed.py",
        "scenario": args.scenario,
        "file": name,
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "ffmpeg": ffmpeg_version(),
        "video": {
            "codec": "h264", "profile": "High", "pixel_format": "yuv420p",
            "width": args.width, "height": args.height, "fps": args.fps,
            "seconds": args.seconds, "audio": "aac (silent)" if args.audio else None,
        },
        "camera_utc_offset_minutes": args.camera_utc_offset_minutes,
        "gps_index": args.scenario != "no-gps" and not args.no_index,
        "gps_block_bytes": GPS_BLOCK_SIZE,
        "records": [
            {**{k: v for k, v in asdict(r).items() if v is not None and v != "" and v is not False},
             "written": written_values(r)}
            for r in records
        ],
        "expected_first_fix": None,
        "limitations": [
            "Synthetic video pattern, not camera footage; no real road damage.",
            "Encoder settings, brands, time base, and GPS padding approximate the camera.",
            "Speed, course, and positions are simulated; projection is not calibrated.",
        ],
    }
    if active:
        first = active[0]
        values = written_values(first)
        if not first.latitude:
            raise AssertionError("first active fix must have coordinates")
        manifest["expected_first_fix"] = {
            "slot": first.slot,
            "recorded_at_utc": (datetime.fromisoformat(first.camera_time) - offset).isoformat() + "Z",
            "latitude": values["latitude"],
            "longitude": values["longitude"],
        }
    manifest_path = target.with_name(target.name + ".json")
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return target, manifest_path, manifest


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--scenario", choices=SCENARIOS, default="standard")
    parser.add_argument("--seconds", type=int, default=12, help="10-15 for seed clips")
    parser.add_argument("--start", default="2026-11-20T12:30:00", help="camera local wall time")
    parser.add_argument("--counter", type=int, default=1)
    parser.add_argument("--camera-utc-offset-minutes", type=int, default=-480)
    parser.add_argument("--width", type=int, default=1920)
    parser.add_argument("--height", type=int, default=1080)
    parser.add_argument("--fps", type=int, default=30)
    parser.add_argument("--audio", action="store_true", help="add a silent AAC track")
    parser.add_argument("--no-index", action="store_true", help="omit moov/gps (scan fallback)")
    parser.add_argument("--validate-exiftool", action="store_true")
    args = parser.parse_args(argv)
    if not 1 <= args.seconds <= 600:
        parser.error("--seconds must be between 1 and 600")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    target, manifest_path, manifest = generate(args)
    print(f"{LABEL}: wrote {target} ({manifest['bytes']} bytes) and {manifest_path}")
    if args.validate_exiftool:
        problems = validate_with_exiftool(target, manifest)
        for problem in problems:
            print(f"ExifTool mismatch: {problem}", file=sys.stderr)
        if problems:
            return 1
        print("ExifTool -ee decoded every active fix as expected")
    return 0


if __name__ == "__main__":
    sys.exit(main())
