"""Metrics calculated from the same persisted rows as the inference pipeline."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session, sessionmaker

from app.models import DetectionRecord, InferenceJob, InputMedia
from app.services.system_metrics import summarize_system_metrics


BASE = datetime(2026, 11, 16, 12, tzinfo=UTC)


def _job(media: InputMedia, status: str, minute: int, *, wait: int = 0,
         processing: int = 0) -> InferenceJob:
    created = BASE + timedelta(minutes=minute)
    started = created + timedelta(seconds=wait) if status in {"running", "completed"} else None
    completed = started + timedelta(seconds=processing) if status == "completed" else None
    return InferenceJob(
        input_media=media, status=status, created_at=created,
        started_at=started, completed_at=completed, captured_at=created,
        latitude=Decimal("33.8823"), longitude=Decimal("-117.8851"),
    )


def test_system_metrics_from_persisted_jobs(session_factory: sessionmaker[Session]) -> None:
    with session_factory() as session, session.begin():
        media = InputMedia(data=b"jpeg", content_type="image/jpeg")
        first = _job(media, "completed", 0, wait=2, processing=8)
        second = _job(media, "completed", 1, wait=6, processing=14)
        failed = _job(media, "failed", 2)
        pending = _job(media, "pending", 3)
        session.add_all((first, second, failed, pending))
        session.flush()
        for job, location in (
            (first, {"latitude": 33.88, "longitude": -117.88}),
            (first, None),
            (second, {"latitude": 33.89, "longitude": -117.89}),
        ):
            session.add(DetectionRecord(
                job_id=job.job_id, issue_type=3, confidence=0.8,
                severity="moderate", bounding_box=[1, 2, 3, 4],
                location=location, detected_at=job.completed_at,
            ))

    with session_factory() as session:
        metrics = summarize_system_metrics(session)
        assert metrics == {
            "submitted_jobs": 4, "pending_jobs": 1, "running_jobs": 0,
            "completed_jobs": 2, "failed_jobs": 1,
            "success_rate": pytest.approx(2 / 3),
            "mean_queue_wait_seconds": 4,
            "mean_processing_seconds": 11,
            "mean_end_to_end_seconds": 15,
            "detections": 3, "located_detections": 2,
            "geolocation_yield": pytest.approx(2 / 3),
        }
        later = summarize_system_metrics(session, submitted_after=BASE + timedelta(minutes=2))
        assert later["submitted_jobs"] == 2
        assert later["failed_jobs"] == 1
        assert later["success_rate"] == 0
        assert later["mean_queue_wait_seconds"] is None
        assert later["geolocation_yield"] is None


def test_empty_window_has_no_rates(session_factory: sessionmaker[Session]) -> None:
    with session_factory() as session:
        metrics = summarize_system_metrics(session)
    assert metrics["submitted_jobs"] == 0
    assert metrics["success_rate"] is None
    assert metrics["mean_end_to_end_seconds"] is None
    assert metrics["geolocation_yield"] is None
