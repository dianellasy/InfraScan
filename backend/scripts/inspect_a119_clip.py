#!/usr/bin/env python3
"""Dry-run an A119 Mini 2 MP4 through InfraScan's video checks without uploading.

Runs the same API-time validation (container, H.264 stream limits, frame
rate, duration, embedded GPS) and the worker's frame sampling and GPS
matching. It does not contact R2, the database, or the model. With
`--extract-dir` it also decodes the sampled frames with FFmpeg, which is the
FFmpeg smoke test for a new machine or container.

Run from backend/:

    ../.venv/bin/python scripts/inspect_a119_clip.py /path/to/20261120123000_000001.MP4 \
        --camera-utc-offset-minutes -480 [--frames] [--extract-dir /tmp/a119-frames]

Output is a JSON summary on stdout. Per-frame positions are only printed with
`--frames`; nothing is logged.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.api.video_validation import (  # noqa: E402
    EmptyVideo,
    MissingGps,
    VideoTooLarge,
    validate_video_upload,
)
from inference.telemetry import locate_frame, validate_camera_utc_offset  # noqa: E402
from inference.video import (  # noqa: E402
    MAX_VIDEO_BYTES,
    VideoRejected,
    extract_frames,
    probe_timeline,
    select_sample_pts,
)


def inspect(path: Path, offset_minutes: int, *, frames: bool, extract_dir: Path | None) -> dict:
    with open(path, "rb") as source:
        validated = validate_video_upload(source, offset_minutes, MAX_VIDEO_BYTES)
    info, track = validated.info, validated.track
    timeline = probe_timeline(path, info)
    selected = select_sample_pts(timeline.pts)
    first = track.first_fix

    sampled = []
    for pts in selected:
        seconds = timeline.seconds(pts)
        location = locate_frame(track.fixes, seconds)
        entry = {"frame_timestamp": round(seconds, 3), "gps": location.method if location else None}
        if frames and location is not None:
            entry.update(
                latitude=location.latitude,
                longitude=location.longitude,
                heading_deg=location.heading_deg,
            )
        sampled.append(entry)

    report = {
        "result": "accepted",
        "file": path.name,
        "bytes": path.stat().st_size,
        "video": {
            "width": info.width,
            "height": info.height,
            "duration_seconds": round(info.duration_seconds, 3),
            "profile": info.profile,
            "pixel_format": info.pixel_format,
            "average_fps": round(info.average_fps, 3),
            "peak_frames_per_second": timeline.peak_frames_per_second,
            "has_audio": info.has_audio,
        },
        "gps": {
            "source": track.source,
            "records": track.record_count,
            "fixes": len(track.fixes),
            "void": track.void_count,
            "invalid": track.invalid_count,
            "duplicates": track.duplicate_count,
            "conflicting_duplicates": track.conflicting_duplicate_count,
            "discarded_epochs": track.discarded_epoch_count,
            "out_of_range": track.out_of_range_count,
            "cold_start_clock_jump_seconds": track.cold_start_clock_jump_seconds,
        },
        "submission": {
            "camera_utc_offset_minutes": offset_minutes,
            "captured_at": first.recorded_at.isoformat().replace("+00:00", "Z"),
            "latitude": first.latitude,
            "longitude": first.longitude,
            "first_fix_frame_timestamp": round(first.frame_offset_seconds, 3),
        },
        "sampling": {
            "sampled_frames": len(selected),
            "frames_with_gps": sum(item["gps"] is not None for item in sampled),
            "frames": sampled,
        },
    }
    if extract_dir is not None:
        extract_dir.mkdir(parents=True, exist_ok=True)
        if any(extract_dir.iterdir()):
            raise SystemExit(f"--extract-dir {extract_dir} must be empty")
        decoded = extract_frames(path, info, timeline, selected, extract_dir)
        report["sampling"]["decoded_frames"] = len(decoded)
        report["sampling"]["decoded_dir"] = str(extract_dir)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("path", type=Path)
    parser.add_argument("--camera-utc-offset-minutes", type=int, required=True)
    parser.add_argument("--frames", action="store_true", help="print per-frame positions")
    parser.add_argument("--extract-dir", type=Path, help="decode sampled frames here (FFmpeg smoke)")
    args = parser.parse_args(argv)
    try:
        offset = validate_camera_utc_offset(args.camera_utc_offset_minutes)
    except ValueError as exc:
        parser.error(str(exc))

    try:
        report = inspect(args.path, offset, frames=args.frames, extract_dir=args.extract_dir)
    except VideoTooLarge:
        report = {"result": "rejected", "status": 413, "code": "FILE_TOO_LARGE"}
    except EmptyVideo:
        report = {"result": "rejected", "status": 422, "code": "EMPTY_FILE"}
    except VideoRejected as exc:
        status = 415 if exc.kind == "unsupported" else 422
        report = {"result": "rejected", "status": status, "code": exc.code, "message": str(exc)}
    except MissingGps as exc:
        report = {"result": "rejected", "status": 422, "code": "MISSING_GPS_TRACK", "message": str(exc)}
    print(json.dumps(report, indent=2))
    return 0 if report["result"] == "accepted" else 1


if __name__ == "__main__":
    sys.exit(main())
