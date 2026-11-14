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
from app.geolocation import PocGeolocationConfig
from app.models import DetectionRecord, InferenceJob
from app.repositories import (
    DetectionRecordRepository,
    EvidenceMediaRepository,
    InferenceJobRepository,
    InputMediaRepository,
)
from app.schemas import (
    DetectionDTO,
    DetectionEvidenceDTO,
    DetectionLocation,
    DetectionLocationDetails,
    EvidenceFrameDTO,
    InferenceJobAccepted,
    InferenceJobDTO,
    InferenceMetadata,
    InferenceSubmissionMetadata,
    JobGeolocationDTO,
    Page,
)
from app.services.contracts import (
    DetectionNotFoundError,
    JobNotFoundError,
    MediaContent,
    MediaKind,
    MediaNotFoundError,
    MediaUrlBuilder,
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


def _media_url(
    urls: MediaUrlBuilder | None, kind: MediaKind, media_id: UUID | None
) -> str | None:
    if urls is None or media_id is None:
        return None
    return urls.media_url(kind, media_id)


def job_to_dto(
    job: InferenceJob,
    annotated_media_id: UUID | None = None,
    urls: MediaUrlBuilder | None = None,
) -> InferenceJobDTO:
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
        geolocation=(
            JobGeolocationDTO.model_validate(job.geolocation)
            if job.geolocation is not None
            else None
        ),
        annotated_media_id=annotated_media_id,
        annotated_image_url=_media_url(urls, "evidence", annotated_media_id),
    )


def detection_to_dto(
    record: DetectionRecord, urls: MediaUrlBuilder | None = None
) -> DetectionDTO:
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
        evidence_media_url=_media_url(urls, "evidence", record.evidence_media),
        location=location,
        location_status=record.location_status,
        location_details=(
            DetectionLocationDetails.model_validate(record.location_details)
            if record.location_details is not None
            else None
        ),
        bounding_box=list(record.bounding_box),
        frame_timestamp=record.frame_timestamp,
    )


class DatabaseInfraScanService:
    """PostgreSQL-backed `InfraScanService` for inference submission and tracking.

    Every database or broker call is blocking and runs in Starlette's thread
    pool with its own short-lived session, so the event loop is never blocked
    and no session outlives one operation.

    With `poc_geolocation` set, every accepted submission is persisted with
    that configuration's source GPS (ignoring the request's coordinates) and
    its camera provenance, which the worker uses to geolocate detections.
    """

    def __init__(
        self,
        session_factory: Callable[[], Session],
        queue: InferenceQueueAdapter,
        poc_geolocation: PocGeolocationConfig | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._queue = queue
        self._poc_geolocation = poc_geolocation

    @property
    def poc_geolocation(self) -> PocGeolocationConfig | None:
        return self._poc_geolocation

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
        geolocation = None
        poc = self._poc_geolocation
        if poc is not None:
            geolocation = poc.provenance(metadata.latitude, metadata.longitude)
            metadata = metadata.model_copy(
                update={
                    "latitude": poc.source.latitude,
                    "longitude": poc.source.longitude,
                }
            )
        try:
            with self._session_factory() as session, session.begin():
                media = InputMediaRepository(session).store(
                    data, SUBMITTED_CONTENT_TYPE, filename
                )
                job = InferenceJobRepository(session).create_pending(
                    media, metadata, geolocation
                )
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

    async def get_job(
        self, job_id: UUID, urls: MediaUrlBuilder | None = None
    ) -> InferenceJobDTO:
        job = await run_in_threadpool(self._load_job, job_id, urls)
        if job is None:
            raise JobNotFoundError(job_id)
        return job

    def _load_job(
        self, job_id: UUID, urls: MediaUrlBuilder | None
    ) -> InferenceJobDTO | None:
        with self._session_factory() as session:
            job = InferenceJobRepository(session).get(job_id)
            if job is None:
                return None
            evidence_id = EvidenceMediaRepository(session).id_for_job(job_id)
            return job_to_dto(job, evidence_id, urls)

    async def list_job_detections(
        self,
        job_id: UUID,
        page: int,
        page_size: int,
        urls: MediaUrlBuilder | None = None,
    ) -> Page[DetectionDTO]:
        detections = await run_in_threadpool(
            self._load_job_detections, job_id, page, page_size, urls
        )
        if detections is None:
            raise JobNotFoundError(job_id)
        return detections

    def _load_job_detections(
        self,
        job_id: UUID,
        page: int,
        page_size: int,
        urls: MediaUrlBuilder | None,
    ) -> Page[DetectionDTO] | None:
        with self._session_factory() as session:
            if not InferenceJobRepository(session).exists(job_id):
                return None
            records, total = DetectionRecordRepository(session).list_for_job(
                job_id, offset=(page - 1) * page_size, limit=page_size
            )
            return Page[DetectionDTO](
                items=[detection_to_dto(record, urls) for record in records],
                total=total,
                page=page,
                page_size=page_size,
            )

    async def get_media(self, kind: MediaKind, media_id: UUID) -> MediaContent:
        media = await run_in_threadpool(self._load_media, kind, media_id)
        if media is None:
            raise MediaNotFoundError(media_id)
        return media

    def _load_media(self, kind: MediaKind, media_id: UUID) -> MediaContent | None:
        with self._session_factory() as session:
            repository = (
                EvidenceMediaRepository(session)
                if kind == "evidence"
                else InputMediaRepository(session)
            )
            media = repository.get(media_id)
            if media is None:
                return None
            return MediaContent(
                media_id=media.media_id, data=media.data, content_type=media.content_type
            )

    async def get_detection_evidence(
        self, detection_id: UUID, urls: MediaUrlBuilder
    ) -> DetectionEvidenceDTO:
        evidence = await run_in_threadpool(self._load_detection_evidence, detection_id, urls)
        if evidence is None:
            raise DetectionNotFoundError(detection_id)
        return evidence

    def _load_detection_evidence(
        self, detection_id: UUID, urls: MediaUrlBuilder
    ) -> DetectionEvidenceDTO | None:
        with self._session_factory() as session:
            record = DetectionRecordRepository(session).get(detection_id)
            if record is None:
                return None
            x1, y1, x2, y2 = record.bounding_box
            return DetectionEvidenceDTO(
                detection_id=record.detection_id,
                frames=[
                    EvidenceFrameDTO(
                        timestamp_seconds=record.frame_timestamp,
                        original_url=urls.media_url("input", record.job.input_media_id),
                        annotated_url=_media_url(urls, "evidence", record.evidence_media),
                        bounding_box={"x1": x1, "y1": y1, "x2": x2, "y2": y2},
                    )
                ],
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

    async def get_analytics_overview(self, *args: Any, **kwargs: Any) -> NoReturn:
        raise NotImplementedError

    async def get_road_conditions(self, *args: Any, **kwargs: Any) -> NoReturn:
        raise NotImplementedError

    async def get_geographic_analytics(self, *args: Any, **kwargs: Any) -> NoReturn:
        raise NotImplementedError

    async def get_damage_distribution(self, *args: Any, **kwargs: Any) -> NoReturn:
        raise NotImplementedError
