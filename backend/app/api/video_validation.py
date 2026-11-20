"""API-time validation of A119 Mini 2 MP4 uploads.

The spooled upload is copied, with a hard byte cap, to a named temporary file
so ffprobe and the GPS parser can read a local path. The copy is always
deleted; the spooled original is what gets streamed to object storage.
"""

from __future__ import annotations

import logging
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from inference.telemetry import GpsTrack, TelemetryError, read_a119_track
from inference.video import MAX_VIDEO_BYTES, VideoInfo, VideoRejected, inspect_video

logger = logging.getLogger(__name__)

_COPY_CHUNK_BYTES = 1024 * 1024
UNSUPPORTED_BRANDS = frozenset({b"qt  "})


def looks_like_iso_bmff(head: bytes) -> bool:
    """True when the first box is an `ftyp` box (ISO Base Media / MP4)."""
    if len(head) < 12 or head[4:8] != b"ftyp":
        return False
    size = int.from_bytes(head[0:4], "big")
    return size == 1 or size >= 16


class VideoTooLarge(ValueError):
    pass


class EmptyVideo(ValueError):
    pass


class MissingGps(ValueError):
    pass


@dataclass(frozen=True)
class ValidatedVideo:
    info: VideoInfo
    track: GpsTrack


def _copy_bounded(source: BinaryIO, target: BinaryIO, max_bytes: int) -> int:
    source.seek(0)
    total = 0
    while chunk := source.read(_COPY_CHUNK_BYTES):
        total += len(chunk)
        if total > max_bytes:
            raise VideoTooLarge
        target.write(chunk)
    target.flush()
    return total


def validate_video_upload(
    source: BinaryIO, camera_utc_offset_minutes: int, max_bytes: int = MAX_VIDEO_BYTES
) -> ValidatedVideo:
    """Blocking: run in a worker thread. Raises a typed rejection on failure."""
    source.seek(0)
    head = source.read(16)
    if not head:
        raise EmptyVideo
    if not looks_like_iso_bmff(head):
        raise VideoRejected(
            "unsupported", "UNSUPPORTED_MEDIA_FORMAT", "Uploaded video must be an MP4 file."
        )
    if head[8:12] in UNSUPPORTED_BRANDS:
        raise VideoRejected(
            "unsupported",
            "UNSUPPORTED_MEDIA_FORMAT",
            "QuickTime .mov files are not supported; upload an MP4.",
        )

    fd, name = tempfile.mkstemp(prefix="infrascan-upload-", suffix=".mp4")
    path = Path(name)
    try:
        with os.fdopen(fd, "wb") as target:
            _copy_bounded(source, target, max_bytes)
        info, _ = inspect_video(path)
        try:
            track = read_a119_track(path, camera_utc_offset_minutes, info.duration_seconds)
        except TelemetryError as exc:
            raise MissingGps(str(exc)) from exc
        logger.info(
            "Validated MP4 %dx%d %.1fs: %d GPS fixes via %s (void=%d invalid=%d "
            "duplicates=%d conflicting=%d discarded=%d)",
            info.width, info.height, info.duration_seconds, len(track.fixes),
            track.source, track.void_count, track.invalid_count, track.duplicate_count,
            track.conflicting_duplicate_count, track.discarded_epoch_count,
        )
        return ValidatedVideo(info, track)
    finally:
        path.unlink(missing_ok=True)
        source.seek(0)
