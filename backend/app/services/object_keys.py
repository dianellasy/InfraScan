"""Deterministic, collision-safe object keys. Keys never start with "/"."""

from __future__ import annotations

from uuid import UUID

INPUT_EXTENSIONS = {"image/jpeg": "jpg", "video/mp4": "mp4"}


def input_media_key(media_id: UUID, content_type: str) -> str:
    return f"input/{media_id}.{INPUT_EXTENSIONS[content_type]}"


def direct_upload_key(user_id: UUID, media_id: UUID, content_type: str) -> str:
    """Key for a browser upload. The owner is part of the key, so a user can
    only ever start a job from an object signed for their own uploads."""
    return f"input/{user_id}/{media_id}.{INPUT_EXTENSIONS[content_type]}"


def evidence_media_key(job_id: UUID, media_id: UUID, frame_timestamp: float | None) -> str:
    if frame_timestamp is None:
        return f"evidence/{job_id}/{media_id}.jpg"
    # Zero-padded milliseconds keep a job's video evidence listed in frame order.
    return f"evidence/{job_id}/{round(frame_timestamp * 1000):08d}-{media_id}.jpg"
