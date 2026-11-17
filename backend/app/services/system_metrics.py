"""System metrics derivable from persisted image jobs and detections."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import DetectionRecord, InferenceJob


def summarize_system_metrics(
    session: Session, *, submitted_after: datetime | None = None,
    submitted_before: datetime | None = None,
) -> dict[str, int | float | None]:
    """Summarize jobs submitted in [after, before), including their detections.

    Success rate uses terminal jobs only. Durations use completed jobs only;
    failed jobs have no persisted failure timestamp. Empty denominators yield
    None rather than a misleading zero rate or duration.
    """
    filters = []
    if submitted_after is not None:
        filters.append(InferenceJob.created_at >= submitted_after)
    if submitted_before is not None:
        filters.append(InferenceJob.created_at < submitted_before)

    jobs = session.execute(
        select(
            InferenceJob.status, InferenceJob.created_at,
            InferenceJob.started_at, InferenceJob.completed_at,
        ).where(*filters)
    ).all()
    counts = {status: 0 for status in ("pending", "running", "completed", "failed")}
    queue_seconds = []
    processing_seconds = []
    total_seconds = []
    for status, created, started, completed in jobs:
        counts[status] += 1
        if status == "completed" and started is not None and completed is not None:
            queue_seconds.append((started - created).total_seconds())
            processing_seconds.append((completed - started).total_seconds())
            total_seconds.append((completed - created).total_seconds())

    locations = session.scalars(
        select(DetectionRecord.location.is_not(None))
        .join(InferenceJob, DetectionRecord.job_id == InferenceJob.job_id)
        .where(*filters)
    ).all()
    detection_count = len(locations)
    located_count = sum(locations)
    terminal_count = counts["completed"] + counts["failed"]

    def mean(values: list[float]) -> float | None:
        return sum(values) / len(values) if values else None

    return {
        "submitted_jobs": len(jobs),
        "pending_jobs": counts["pending"],
        "running_jobs": counts["running"],
        "completed_jobs": counts["completed"],
        "failed_jobs": counts["failed"],
        "success_rate": counts["completed"] / terminal_count if terminal_count else None,
        "mean_queue_wait_seconds": mean(queue_seconds),
        "mean_processing_seconds": mean(processing_seconds),
        "mean_end_to_end_seconds": mean(total_seconds),
        "detections": detection_count,
        "located_detections": located_count,
        "geolocation_yield": located_count / detection_count if detection_count else None,
    }
