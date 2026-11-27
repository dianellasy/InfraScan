"""VIOFO A119 Mini 2 GPS telemetry, independent of the model and database."""

from __future__ import annotations

import os
from pathlib import Path
from typing import BinaryIO

from inference.telemetry.a119 import TelemetryError, read_record_slots
from inference.telemetry.matching import FrameLocation, locate_frame
from inference.telemetry.track import (
    GpsFix,
    GpsTrack,
    MissingGpsTrack,
    build_track,
    validate_camera_utc_offset,
)

__all__ = [
    "FrameLocation",
    "GpsFix",
    "GpsTrack",
    "MissingGpsTrack",
    "TelemetryError",
    "build_track",
    "locate_frame",
    "read_a119_track",
    "validate_camera_utc_offset",
]


def read_a119_track(
    source: str | Path | BinaryIO,
    camera_utc_offset_minutes: int,
    duration_seconds: float | None = None,
) -> GpsTrack:
    """Parse the embedded A119 Mini 2 track from a local path or seekable file."""
    if isinstance(source, (str, Path)):
        with open(source, "rb") as f:
            return read_a119_track(f, camera_utc_offset_minutes, duration_seconds)
    file_size = source.seek(0, os.SEEK_END)
    slots, origin = read_record_slots(source, file_size)
    if not slots:
        raise MissingGpsTrack("The video has no embedded A119 GPS records.")
    return build_track(slots, origin, camera_utc_offset_minutes, duration_seconds)
