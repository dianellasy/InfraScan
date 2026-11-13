from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, NoReturn
from uuid import UUID

from fastapi import UploadFile
from sqlalchemy.exc import InterfaceError, OperationalError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.adapters.ports import InferenceQueueAdapter
from app.models import DetectionRecord, InferenceJob
from app.repositories import (
    DetectionRecordRepository,
    InferenceJobRepository,
    InputMediaRepository,
)
from app.schemas import (
    DetectionDTO,
    DetectionLocation,
    InferenceJobAccepted,
    InferenceJobDTO,
    InferenceMetadata,
    InferenceSubmissionMetadata,
    Page,
)
from app.services.contracts import (
    JobNotFoundError,
    SubmissionDependencyUnavailableError,
)
from inference.labels import class_label

logger = logging.getLogger(__name__)

# The controller has already verified the bytes decode as JPEG, so store the
# verified type rather than the client-declared one.
SUBMITTED_CONTENT_TYPE = "image/jpeg"

_DATABASE_UNAVAILABLE_ERRORS = (OperationalError, InterfaceError, PoolTimeoutError)


def _utc(value: datetime | None) -> datetime | None:
    return value.astimezone(UTC) if value is not None else None


def job_to_dto(job: InferenceJob) -> InferenceJobDTO:
    return InferenceJobDTO(
        job_id=job.job_id,
        status=job.status,
        submitted_at=_utc(job.created_at),
        started_at=_utc(job.started_at),
        completed_at=_utc(job.completed_at),
        metadata=InferenceMetadata(
            captured_at=_utc(job.captured_at),
            latitude=float(job.latitude),
            longitude=float(job.longitude),
        ),
        model_version=job.model_version,
    )


def detection_to_dto(record: DetectionRecord) -> DetectionDTO:
    location = (
        DetectionLocation.model_validate(record.location)
        if record.location is not None
        else None
    )
    return DetectionDTO(
        detection_id=record.detection_id,
        job_id=record.job_id,
        classification=class_label(record.issue_type),
        severity=record.severity,
        confidence=record.confidence,
        latitude=location.latitude if location else None,
        longitude=location.longitude if location else None,
        detected_at=_utc(record.detected_at),
        issue_id=record.issue_id,
        issue_type=record.issue_type,
        evidence_media=record.evidence_media,
        location=location,
        bounding_box=list(record.bounding_box),
        frame_timestamp=record.frame_timestamp,
    )


class DatabaseInfraScanService:
    """PostgreSQL-backed `InfraScanService` for inference submission and tracking.

    Every database or broker call is blocking and runs in Starlette's thread
    pool with its own short-lived session, so the event loop is never blocked
    and no session outlives one operation.
    """

    def __init__(
        self,
        session_factory: Callable[[], Session],
        queue: InferenceQueueAdapter,
    ) -> None:
        self._session_factory = session_factory
        self._queue = queue

    async def submit_inference(
        self, file: UploadFile, metadata: InferenceSubmissionMetadata
    ) -> InferenceJobAccepted:
        await file.seek(0)
        data = await file.read()
        accepted = await run_in_threadpool(
            self._create_pending_job, data, file.filename, metadata
        )
        await run_in_threadpool(self._publish, accepted.job_id)
        return accepted

    def _create_pending_job(
        self,
        data: bytes,
        filename: str | None,
        metadata: InferenceSubmissionMetadata,
    ) -> InferenceJobAccepted:
        try:
            with self._session_factory() as session, session.begin():
                media = InputMediaRepository(session).store(
                    data, SUBMITTED_CONTENT_TYPE, filename
                )
                job = InferenceJobRepository(session).create_pending(media, metadata)
                accepted = InferenceJobAccepted(
                    job_id=job.job_id,
                    status="pending",
                    submitted_at=job.created_at,
                )
        except _DATABASE_UNAVAILABLE_ERRORS as exc:
            logger.exception("Could not persist inference submission")
            raise SubmissionDependencyUnavailableError from exc
        return accepted

    def _publish(self, job_id: UUID) -> None:
        try:
            self._queue.enqueue(job_id)
        except Exception as exc:
            logger.exception("Could not enqueue inference job %s", job_id)
            self._mark_failed_after_publish_error(job_id)
            raise SubmissionDependencyUnavailableError from exc

    def _mark_failed_after_publish_error(self, job_id: UUID) -> None:
        try:
            with self._session_factory() as session, session.begin():
                InferenceJobRepository(session).mark_failed(job_id)
        except Exception:
            logger.exception(
                "Could not mark inference job %s failed after enqueue error", job_id
            )

    async def get_job(self, job_id: UUID) -> InferenceJobDTO:
        job = await run_in_threadpool(self._load_job, job_id)
        if job is None:
            raise JobNotFoundError(job_id)
        return job

    def _load_job(self, job_id: UUID) -> InferenceJobDTO | None:
        with self._session_factory() as session:
            job = InferenceJobRepository(session).get(job_id)
            return job_to_dto(job) if job is not None else None

    async def list_job_detections(
        self, job_id: UUID, page: int, page_size: int
    ) -> Page[DetectionDTO]:
        detections = await run_in_threadpool(
            self._load_job_detections, job_id, page, page_size
        )
        if detections is None:
            raise JobNotFoundError(job_id)
        return detections

    def _load_job_detections(
        self, job_id: UUID, page: int, page_size: int
    ) -> Page[DetectionDTO] | None:
        with self._session_factory() as session:
            if not InferenceJobRepository(session).exists(job_id):
                return None
            records, total = DetectionRecordRepository(session).list_for_job(
                job_id, offset=(page - 1) * page_size, limit=page_size
            )
            return Page[DetectionDTO](
                items=[detection_to_dto(record) for record in records],
                total=total,
                page=page,
                page_size=page_size,
            )

    # Not part of the inference MVP; the API maps these to 501 Not Implemented.
    async def list_jobs(self, *args: Any, **kwargs: Any) -> NoReturn:
        raise NotImplementedError

    async def list_issues(self, *args: Any, **kwargs: Any) -> NoReturn:
        raise NotImplementedError

    async def get_issue(self, *args: Any, **kwargs: Any) -> NoReturn:
        raise NotImplementedError

    async def create_issue(self, *args: Any, **kwargs: Any) -> NoReturn:
        raise NotImplementedError

    async def update_issue(self, *args: Any, **kwargs: Any) -> NoReturn:
        raise NotImplementedError

    async def list_review_queue(self, *args: Any, **kwargs: Any) -> NoReturn:
        raise NotImplementedError

    async def list_roads(self, *args: Any, **kwargs: Any) -> NoReturn:
        raise NotImplementedError

    async def get_road(self, *args: Any, **kwargs: Any) -> NoReturn:
        raise NotImplementedError

    async def list_road_issues(self, *args: Any, **kwargs: Any) -> NoReturn:
        raise NotImplementedError

    async def list_detections(self, *args: Any, **kwargs: Any) -> NoReturn:
        raise NotImplementedError

    async def get_detection(self, *args: Any, **kwargs: Any) -> NoReturn:
        raise NotImplementedError

    async def get_detection_evidence(self, *args: Any, **kwargs: Any) -> NoReturn:
        raise NotImplementedError

    async def get_analytics_overview(self, *args: Any, **kwargs: Any) -> NoReturn:
        raise NotImplementedError

    async def get_road_conditions(self, *args: Any, **kwargs: Any) -> NoReturn:
        raise NotImplementedError

    async def get_geographic_analytics(self, *args: Any, **kwargs: Any) -> NoReturn:
        raise NotImplementedError

    async def get_damage_distribution(self, *args: Any, **kwargs: Any) -> NoReturn:
        raise NotImplementedError
