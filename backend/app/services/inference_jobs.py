from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, BinaryIO, NoReturn
from uuid import UUID, uuid4

from fastapi import UploadFile
from sqlalchemy.exc import InterfaceError, OperationalError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.adapters.ports import InferenceQueueAdapter, MediaStorageAdapter
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
    ResolvedVideoSubmission,
)
from app.services.contracts import (
    EvidenceNotFoundError,
    IssueNotFoundError,
    JobNotFoundError,
    SubmissionDependencyUnavailableError,
)
from app.services.dto import detection_to_dto
from app.services.issues import EvidenceImage, IssueQueries
from app.services.object_keys import input_media_key

__all__ = ["DatabaseInfraScanService", "detection_to_dto", "job_to_dto"]

logger = logging.getLogger(__name__)

# The controller verified the bytes, so store the detected type rather than
# the client-declared one.
IMAGE_CONTENT_TYPE = "image/jpeg"
VIDEO_CONTENT_TYPE = "video/mp4"

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
        media_storage: MediaStorageAdapter | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._queue = queue
        self._media_storage = media_storage
        self._issues = IssueQueries(session_factory, media_storage)

    async def submit_inference(
        self, file: UploadFile, metadata: InferenceSubmissionMetadata
    ) -> InferenceJobAccepted:
        """Upload to object storage, commit the job, then publish its ID."""
        if self._media_storage is None:
            logger.error("Object storage is not configured; rejecting submission")
            raise SubmissionDependencyUnavailableError
        is_video = isinstance(metadata, ResolvedVideoSubmission)
        content_type = VIDEO_CONTENT_TYPE if is_video else IMAGE_CONTENT_TYPE
        media_id = uuid4()
        key = input_media_key(media_id, content_type)

        await file.seek(0)
        if is_video:
            # Streamed from the spooled upload; never read into memory.
            await run_in_threadpool(self._upload, key, content_type, file.file, None)
        else:
            # Validation already bounded JPEGs to 5,000,000 bytes.
            data = await file.read()
            await run_in_threadpool(self._upload, key, content_type, None, data)
        accepted = await run_in_threadpool(
            self._create_pending_job, media_id, key, content_type, file.filename, metadata
        )
        await run_in_threadpool(self._publish, accepted.job_id)
        return accepted

    def _upload(
        self, key: str, content_type: str, source: BinaryIO | None, data: bytes | None
    ) -> None:
        try:
            if source is not None:
                self._media_storage.put_file(source, key, content_type)
            else:
                self._media_storage.put(data, key, content_type)
        except Exception as exc:
            logger.exception("Could not upload input media %s", key)
            # A failed multipart upload can leave a partial object behind.
            self._delete_object(key)
            raise SubmissionDependencyUnavailableError from exc

    def _delete_object(self, key: str, job_id: UUID | None = None) -> None:
        try:
            self._media_storage.delete(key)
        except Exception:
            logger.exception("Could not delete object %s (job %s)", key, job_id)

    def _create_pending_job(
        self,
        media_id: UUID,
        key: str,
        content_type: str,
        filename: str | None,
        metadata: InferenceSubmissionMetadata,
    ) -> InferenceJobAccepted:
        try:
            with self._session_factory() as session, session.begin():
                media = InputMediaRepository(session).store(
                    None,
                    content_type,
                    filename,
                    media_id=media_id,
                    object_storage_key=key,
                )
                job = InferenceJobRepository(session).create_pending(media, metadata)
                accepted = InferenceJobAccepted(
                    job_id=job.job_id,
                    status="pending",
                    submitted_at=job.created_at,
                )
        except _DATABASE_UNAVAILABLE_ERRORS as exc:
            logger.exception("Could not persist inference submission")
            self._delete_object(key)
            raise SubmissionDependencyUnavailableError from exc
        except BaseException:
            self._delete_object(key)
            raise
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
