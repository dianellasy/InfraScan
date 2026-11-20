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
from app.models import InferenceJob
from app.repositories import (
    DetectionRecordRepository,
    InferenceJobRepository,
    InputMediaRepository,
)
from app.schemas import (
    BoundingBox,
    DetectionDTO,
    InferenceJobAccepted,
    InferenceJobDTO,
    InferenceMetadata,
    InferenceSubmissionMetadata,
    IssueDTO,
    IssueFeatureCollection,
    IssueFilters,
    Page,
)
from app.services.contracts import (
    EvidenceNotFoundError,
    IssueNotFoundError,
    JobNotFoundError,
    SubmissionDependencyUnavailableError,
)
from app.services.dto import detection_to_dto
from app.services.issues import EvidenceImage, IssueQueries

__all__ = ["DatabaseInfraScanService", "detection_to_dto", "job_to_dto"]

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
        self._issues = IssueQueries(session_factory)

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

    async def list_issue_map(
        self, bbox: BoundingBox, filters: IssueFilters, limit: int
    ) -> IssueFeatureCollection:
        return await run_in_threadpool(self._issues.map_features, bbox, filters, limit)

    async def list_issues(
        self,
        filters: IssueFilters,
        bbox: BoundingBox | None,
        page: int,
        page_size: int,
    ) -> Page[IssueDTO]:
        return await run_in_threadpool(
            self._issues.list_issues, filters, bbox, page, page_size
        )

    async def list_review_queue(self, page: int, page_size: int) -> Page[IssueDTO]:
        return await run_in_threadpool(self._issues.review_queue, page, page_size)

    async def get_issue(self, issue_id: UUID) -> IssueDTO:
        issue = await run_in_threadpool(self._issues.get, issue_id)
        if issue is None:
            raise IssueNotFoundError(issue_id)
        return issue

    async def list_issue_detections(
        self, issue_id: UUID, page: int, page_size: int
    ) -> Page[DetectionDTO]:
        detections = await run_in_threadpool(
            self._issues.detections, issue_id, page, page_size
        )
        if detections is None:
            raise IssueNotFoundError(issue_id)
        return detections

    async def get_evidence_image(self, media_id: UUID) -> EvidenceImage:
        image = await run_in_threadpool(self._issues.evidence, media_id)
        if image is None:
            raise EvidenceNotFoundError(media_id)
        return image

    # Not part of the inference MVP; the API maps these to 501 Not Implemented.
    async def list_jobs(self, *args: Any, **kwargs: Any) -> NoReturn:
        raise NotImplementedError

    async def create_issue(self, *args: Any, **kwargs: Any) -> NoReturn:
        raise NotImplementedError

    async def update_issue(self, *args: Any, **kwargs: Any) -> NoReturn:
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
