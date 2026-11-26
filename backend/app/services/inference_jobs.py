from __future__ import annotations

import logging
import tempfile
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, BinaryIO, NoReturn
from uuid import UUID, uuid4

from fastapi import UploadFile
from sqlalchemy.exc import InterfaceError, OperationalError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.adapters.ports import (
    DirectUploadStorageAdapter,
    InferenceQueueAdapter,
    MediaStorageAdapter,
)
from app.authorization import JobAccess
from app.models import InferenceJob, Issue, IssueStatus
from app.repositories import (
    DetectionFilters,
    DetectionRecordRepository,
    DetectionRegisterRepository,
    InferenceJobRepository,
    InputMediaRepository,
)
from app.schemas import (
    BoundingBox,
    DetectionDTO,
    DirectUploadTicket,
    InferenceJobAccepted,
    InferenceJobDTO,
    InferenceMetadata,
    InferenceSubmissionMetadata,
    IssueDTO,
    IssueFeatureCollection,
    IssueFilters,
    IssueUpdate,
    Page,
    ResolvedVideoSubmission,
)
from app.services.contracts import (
    DetectionNotFoundError,
    EvidenceNotFoundError,
    IssueNotFoundError,
    JobNotFoundError,
    SubmissionDependencyUnavailableError,
    UploadAlreadySubmittedError,
    UploadNotFoundError,
    UploadTooLargeError,
)
from app.services.dto import detection_to_dto
from app.services.issues import EvidenceImage, IssueQueries
from app.services.object_keys import direct_upload_key, input_media_key
from inference.labels import CLASS_LABELS

# A signed upload URL is valid this long; the browser starts the PUT at once.
DIRECT_UPLOAD_URL_TTL_SECONDS = 15 * 60

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
        self, file: UploadFile, metadata: InferenceSubmissionMetadata, user_id: UUID
    ) -> InferenceJobAccepted:
        """Upload to object storage, commit the job owned by `user_id`, then publish its ID."""
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
            self._create_pending_job,
            media_id,
            key,
            content_type,
            file.filename,
            metadata,
            user_id,
        )
        await run_in_threadpool(self._publish, accepted.job_id)
        return accepted

    # --- Direct browser uploads -------------------------------------------------
    #
    # 1. `create_direct_upload` signs a PUT URL for a new upload ID.
    # 2. The browser PUTs the file straight to object storage (no API body limit).
    # 3. `fetch_direct_upload` gives the API a local copy to validate with the
    #    same checks as a multipart upload.
    # 4. `submit_direct_upload` commits the job and publishes it, exactly as
    #    `submit_inference` does after its own upload.

    def _direct_storage(self) -> DirectUploadStorageAdapter:
        storage = self._media_storage
        if storage is None or not hasattr(storage, "presign_put"):
            logger.error("Direct uploads need object storage that can sign URLs")
            raise SubmissionDependencyUnavailableError
        return storage  # type: ignore[return-value]

    async def create_direct_upload(
        self, content_type: str, size_bytes: int, user_id: UUID
    ) -> DirectUploadTicket:
        storage = self._direct_storage()
        upload_id = uuid4()
        key = direct_upload_key(user_id, upload_id, content_type)
        try:
            url = await run_in_threadpool(
                storage.presign_put, key, content_type, size_bytes, DIRECT_UPLOAD_URL_TTL_SECONDS
            )
        except Exception as exc:
            logger.exception("Could not sign a direct upload URL")
            raise SubmissionDependencyUnavailableError from exc
        return DirectUploadTicket(
            upload_id=upload_id,
            upload_url=url,
            headers={"Content-Type": content_type},
            expires_in_seconds=DIRECT_UPLOAD_URL_TTL_SECONDS,
        )

    async def fetch_direct_upload(
        self, upload_id: UUID, content_type: str, user_id: UUID, max_bytes: int
    ) -> tuple[BinaryIO, int]:
        """Download the caller's uploaded object to a local spooled file for validation."""
        storage = self._direct_storage()
        key = direct_upload_key(user_id, upload_id, content_type)
        return await run_in_threadpool(
            self._fetch_direct_upload, storage, upload_id, key, max_bytes
        )

    def _fetch_direct_upload(
        self, storage: DirectUploadStorageAdapter, upload_id: UUID, key: str, max_bytes: int
    ) -> tuple[BinaryIO, int]:
        try:
            with self._session_factory() as session:
                if InputMediaRepository(session).exists(upload_id):
                    raise UploadAlreadySubmittedError(upload_id)
            size = storage.size(key)
        except UploadAlreadySubmittedError:
            raise
        except Exception as exc:
            logger.exception("Could not look up direct upload %s", upload_id)
            raise SubmissionDependencyUnavailableError from exc
        if size is None:
            raise UploadNotFoundError(upload_id)
        if size > max_bytes:
            self._delete_object(key)
            raise UploadTooLargeError(size)
        # JPEGs stay in memory; videos spill to the temp directory.
        local = tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024)
        try:
            storage.download_to_file(key, local, max_bytes)
        except Exception as exc:
            local.close()
            logger.exception("Could not download direct upload %s", upload_id)
            raise SubmissionDependencyUnavailableError from exc
        local.seek(0)
        return local, size

    async def discard_direct_upload(
        self, upload_id: UUID, content_type: str, user_id: UUID
    ) -> None:
        """Delete an upload that failed validation (never one a job already uses)."""
        if self._media_storage is None:
            return
        key = direct_upload_key(user_id, upload_id, content_type)
        await run_in_threadpool(self._delete_object, key)

    async def submit_direct_upload(
        self,
        upload_id: UUID,
        content_type: str,
        filename: str | None,
        metadata: InferenceSubmissionMetadata,
        user_id: UUID,
    ) -> InferenceJobAccepted:
        self._direct_storage()
        key = direct_upload_key(user_id, upload_id, content_type)
        accepted = await run_in_threadpool(
            self._create_pending_job,
            upload_id,
            key,
            content_type,
            filename,
            metadata,
            user_id,
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
        user_id: UUID,
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
                job = InferenceJobRepository(session).create_pending(
                    media, metadata, user_id
                )
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

    async def get_job(self, job_id: UUID, access: JobAccess) -> InferenceJobDTO:
        job = await run_in_threadpool(self._load_job, job_id, access)
        if job is None:
            raise JobNotFoundError(job_id)
        return job

    def _load_job(self, job_id: UUID, access: JobAccess) -> InferenceJobDTO | None:
        with self._session_factory() as session:
            job = InferenceJobRepository(session).get_accessible(job_id, access)
            return job_to_dto(job) if job is not None else None

    async def list_job_detections(
        self, job_id: UUID, page: int, page_size: int, access: JobAccess
    ) -> Page[DetectionDTO]:
        detections = await run_in_threadpool(
            self._load_job_detections, job_id, page, page_size, access
        )
        if detections is None:
            raise JobNotFoundError(job_id)
        return detections

    def _load_job_detections(
        self, job_id: UUID, page: int, page_size: int, access: JobAccess
    ) -> Page[DetectionDTO] | None:
        with self._session_factory() as session:
            if not InferenceJobRepository(session).exists(job_id, access):
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
        self, bbox: BoundingBox, filters: IssueFilters, limit: int, access: JobAccess
    ) -> IssueFeatureCollection:
        return await run_in_threadpool(
            self._issues.map_features, bbox, filters, limit, access
        )

    async def list_issues(
        self,
        filters: IssueFilters,
        bbox: BoundingBox | None,
        page: int,
        page_size: int,
        access: JobAccess,
    ) -> Page[IssueDTO]:
        return await run_in_threadpool(
            self._issues.list_issues, filters, bbox, page, page_size, access
        )

    async def list_review_queue(
        self, page: int, page_size: int, access: JobAccess
    ) -> Page[IssueDTO]:
        return await run_in_threadpool(self._issues.review_queue, page, page_size, access)

    async def get_issue(self, issue_id: UUID, access: JobAccess) -> IssueDTO:
        issue = await run_in_threadpool(self._issues.get, issue_id, access)
        if issue is None:
            raise IssueNotFoundError(issue_id)
        return issue

    async def list_issue_detections(
        self, issue_id: UUID, page: int, page_size: int, access: JobAccess
    ) -> Page[DetectionDTO]:
        detections = await run_in_threadpool(
            self._issues.detections, issue_id, page, page_size, access
        )
        if detections is None:
            raise IssueNotFoundError(issue_id)
        return detections

    async def get_evidence_image(self, media_id: UUID, access: JobAccess) -> EvidenceImage:
        image = await run_in_threadpool(self._issues.evidence, media_id, access)
        if image is None:
            raise EvidenceNotFoundError(media_id)
        return image

    # --- Review and the detection register (privileged routes) -------------------

    async def update_issue(
        self, issue_id: UUID, update: IssueUpdate, reviewer_id: UUID, access: JobAccess
    ) -> IssueDTO:
        found = await run_in_threadpool(self._update_issue, issue_id, update, reviewer_id)
        if not found:
            raise IssueNotFoundError(issue_id)
        issue = await run_in_threadpool(self._issues.get, issue_id, access)
        if issue is None:
            raise IssueNotFoundError(issue_id)
        return issue

    def _update_issue(self, issue_id: UUID, update: IssueUpdate, reviewer_id: UUID) -> bool:
        with self._session_factory() as session, session.begin():
            issue = session.get(Issue, issue_id, with_for_update=True)
            if issue is None:
                return False
            if update.severity is not None:
                issue.severity = update.severity.value
            if update.review_note is not None:
                issue.review_note = update.review_note.strip() or None
            if update.status is not None:
                issue.status = update.status.value
                # A human decision settles whatever flagged it for review;
                # reopening it puts it back in the queue as pending.
                if update.status != IssueStatus.PENDING_REVIEW:
                    issue.review_reason = None
            issue.reviewed_by = reviewer_id
            issue.reviewed_at = datetime.now(UTC)
        return True

    async def list_detections(
        self,
        page: int,
        page_size: int,
        classification: str | None,
        severity: str | None,
        confidence_min: float | None,
        confidence_max: float | None,
        detected_after: datetime | None,
        detected_before: datetime | None,
        min_latitude: float | None,
        min_longitude: float | None,
        max_latitude: float | None,
        max_longitude: float | None,
    ) -> Page[DetectionDTO]:
        class_id = None
        if classification is not None:
            # Labels are what the API shows; an unknown label matches nothing.
            class_id = next(
                (
                    cid
                    for cid, label in CLASS_LABELS.items()
                    if label.lower() == classification.lower()
                ),
                -1,
            )
        filters = DetectionFilters(
            class_id=class_id,
            severity=severity,
            confidence_min=confidence_min,
            confidence_max=confidence_max,
            detected_after=detected_after,
            detected_before=detected_before,
            min_latitude=min_latitude,
            min_longitude=min_longitude,
            max_latitude=max_latitude,
            max_longitude=max_longitude,
        )
        return await run_in_threadpool(self._list_detections, filters, page, page_size)

    def _list_detections(
        self, filters: DetectionFilters, page: int, page_size: int
    ) -> Page[DetectionDTO]:
        with self._session_factory() as session:
            rows, total = DetectionRegisterRepository(session).list(
                filters, offset=(page - 1) * page_size, limit=page_size
            )
            items = [detection_to_dto(record, status) for record, status in rows]
        return Page[DetectionDTO](items=items, total=total, page=page, page_size=page_size)

    async def get_detection(self, detection_id: UUID) -> DetectionDTO:
        detection = await run_in_threadpool(self._get_detection, detection_id)
        if detection is None:
            raise DetectionNotFoundError(detection_id)
        return detection

    def _get_detection(self, detection_id: UUID) -> DetectionDTO | None:
        with self._session_factory() as session:
            row = DetectionRegisterRepository(session).get(detection_id)
            return detection_to_dto(*row) if row is not None else None

    # Not part of the inference MVP; the API maps these to 501 Not Implemented.
    async def list_jobs(self, *args: Any, **kwargs: Any) -> NoReturn:
        raise NotImplementedError

    async def create_issue(self, *args: Any, **kwargs: Any) -> NoReturn:
        raise NotImplementedError

    async def list_roads(self, *args: Any, **kwargs: Any) -> NoReturn:
        raise NotImplementedError

    async def get_road(self, *args: Any, **kwargs: Any) -> NoReturn:
        raise NotImplementedError

    async def list_road_issues(self, *args: Any, **kwargs: Any) -> NoReturn:
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
