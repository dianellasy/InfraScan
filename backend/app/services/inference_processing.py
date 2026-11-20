from __future__ import annotations

import logging
import math
import tempfile
from pathlib import Path
from app.services.video import MAX_VIDEO_BYTES, sampled_frames
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any
from uuid import UUID
from uuid_extensions import uuid7

from sqlalchemy.orm import Session

from app.adapters.ports import MediaStorageAdapter
from app.models import DETECTION_SEVERITIES, InferenceJobStatus
from app.repositories import (
    DetectionRecordRepository,
    EvidenceMediaRepository,
    InferenceJobRepository,
    NewDetection,
)
from inference.geolocation import (
    CameraProfile,
    PROTOTYPE_HEADING_DEG,
    SAMPLE_CAMERA_PROFILE,
    geolocate_detection,
)

logger = logging.getLogger(__name__)

EVIDENCE_CONTENT_TYPE = "image/jpeg"
EVIDENCE_MEDIA_OBJECT_KEY_PREFIX = "/output/upload"

# (image bytes, filename) -> (v0-mvp result JSON, annotated JPEG bytes)
InferenceRunner = Callable[[bytes, str | None], tuple[Mapping[str, Any], bytes]]


class InferenceOutputError(ValueError):
    """The pipeline returned output that does not match the v0-mvp contract."""


@dataclass(frozen=True)
class ParsedInference:
    model_version: str
    detections: list[NewDetection]


@dataclass(frozen=True)
class ClaimedJob:
    image: bytes | None
    object_storage_key: str | None
    filename: str | None
    latitude: float
    longitude: float
    content_type: str = "image/jpeg"


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _number(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise InferenceOutputError(f"{name} must be a number")
    if not math.isfinite(value):
        raise InferenceOutputError(f"{name} must be finite")
    return float(value)


def parse_inference_output(result: Mapping[str, Any]) -> ParsedInference:
    """Convert `inference_service` output into rows, preserving model values."""
    model_version = result.get("model_version")
    if not isinstance(model_version, str) or not model_version:
        raise InferenceOutputError("model_version must be a nonempty string")

    detections = []
    for index, item in enumerate(result.get("detections") or ()):
        # `class_ids` is a scalar class ID despite its plural name.
        class_id = item.get("class_ids")
        if isinstance(class_id, bool) or not isinstance(class_id, int):
            raise InferenceOutputError(f"detections[{index}].class_ids must be an int")

        severity = item.get("severity")
        if severity not in DETECTION_SEVERITIES:
            raise InferenceOutputError(f"detections[{index}].severity is not allowed")

        confidence = _number(item.get("confidence"), f"detections[{index}].confidence")
        if not 0 <= confidence <= 1:
            raise InferenceOutputError(f"detections[{index}].confidence is out of range")

        bbox = item.get("bbox_xyxy")
        if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
            raise InferenceOutputError(f"detections[{index}].bbox_xyxy must have 4 values")

        detections.append(
            NewDetection(
                issue_type=class_id,
                confidence=confidence,
                severity=severity,
                bounding_box=[
                    _number(v, f"detections[{index}].bbox_xyxy") for v in bbox
                ],
            )
        )
    return ParsedInference(model_version=model_version, detections=detections)


class InferenceJobProcessor:
    """Runs one persisted inference job end to end.

    Two short transactions: claim the pending job (mark it running), then
    run inference and geolocation with no session open, then save the evidence
    image to object storage and persist its key, all detections, and the
    completed status. Ordinary failures mark the job failed. Retries,
    duplicate-delivery guarantees, and recovery of stranded jobs are deferred
    until after the MVP.
    """

    def __init__(
        self,
        session_factory: Callable[[], Session],
        run_inference: InferenceRunner,
        clock: Callable[[], datetime] = _utc_now,
        *,
        media_storage: MediaStorageAdapter,
        camera_profile: CameraProfile = SAMPLE_CAMERA_PROFILE,
        heading_deg: float = PROTOTYPE_HEADING_DEG,
    ) -> None:
        self._session_factory = session_factory
        self._run_inference = run_inference
        self._clock = clock
        self._media_storage = media_storage
        self._camera_profile = camera_profile
        if not math.isfinite(heading_deg):
            raise ValueError("Prototype heading must be finite")
        self._heading_deg = heading_deg

    def process(self, job_id: UUID) -> InferenceJobStatus | None:
        """Return the job's final status, or None if this call did not run it."""
        try:
            claimed = self._claim(job_id)
        except Exception:
            logger.exception("Could not start inference job %s", job_id)
            raise
        if claimed is None:
            return None
        if claimed.content_type == "video/mp4":
            return self._process_video(job_id, claimed)
        try:
            image = self._load_input_media(claimed)
            result, annotated_jpeg = self._run_inference(image, claimed.filename)
            detected_at = self._clock()
            parsed = parse_inference_output(result)
            parsed = self._geolocate(parsed, result, claimed)
            logger.info(
                "Job %s prototype geolocation profile=%s heading=%s located=%d/%d",
                job_id, self._camera_profile.name, self._heading_deg,
                sum(d.location is not None for d in parsed.detections),
                len(parsed.detections),
            )
        except Exception:
            logger.exception("Inference failed for job %s", job_id)
            self._mark_failed(job_id)
            return InferenceJobStatus.FAILED

        try:
            self._save_results(job_id, parsed, annotated_jpeg, detected_at)
        except Exception:
            logger.exception("Could not save inference results for job %s", job_id)
            self._mark_failed(job_id)
            return InferenceJobStatus.FAILED

        logger.info(
            "Inference job %s completed with %d detection(s)",
            job_id,
            len(parsed.detections),
        )
        return InferenceJobStatus.COMPLETED

    def _process_video(self, job_id: UUID, claimed: ClaimedJob) -> InferenceJobStatus:
        uploaded = []
        results = []
        try:
            if not claimed.object_storage_key:
                raise RuntimeError("Video input requires an object storage key")
            with tempfile.TemporaryDirectory(prefix="infrascan-video-") as directory:
                path = Path(directory) / "input.mp4"
                with path.open("wb") as target:
                    self._media_storage.download_to(claimed.object_storage_key, target, MAX_VIDEO_BYTES)
                for timestamp, jpeg in sampled_frames(path):
                    output, annotated = self._run_inference(jpeg, f"frame-{timestamp:.6f}.jpg")
                    parsed = parse_inference_output(output)
                    width, height = output.get("image_width"), output.get("image_height")
                    if type(width) is not int or type(height) is not int or min(width, height) <= 0:
                        raise InferenceOutputError("Prediction dimensions must be positive integers")
                    # A single submission GPS point cannot locate moving-camera frames.
                    media_id = uuid7()
                    key = f"videos/evidence/{job_id}/{media_id}.jpg"
                    uploaded.append(key)
                    self._media_storage.put(annotated, key, EVIDENCE_CONTENT_TYPE)
                    results.append((timestamp, media_id, key, parsed, self._clock()))
            if not results:
                raise InferenceOutputError("Video yielded no frames")
            versions = {row[3].model_version for row in results}
            if len(versions) != 1:
                raise InferenceOutputError("Model version changed during video processing")
            with self._session_factory() as session, session.begin():
                jobs = InferenceJobRepository(session)
                job = jobs.get_for_update(job_id)
                if job is None or job.status != InferenceJobStatus.RUNNING:
                    raise RuntimeError("job is no longer running")
                for timestamp, media_id, key, parsed, detected_at in results:
                    evidence = EvidenceMediaRepository(session).add(
                        job_id, media_id, key, EVIDENCE_CONTENT_TYPE,
                        f"frame-{timestamp:.6f}.jpg", frame_timestamp=timestamp,
                    )
                    DetectionRecordRepository(session).add_many(
                        job_id, evidence, parsed.detections, detected_at, frame_timestamp=timestamp
                    )
                jobs.mark_completed(job, results[0][3].model_version, completed_at=self._clock())
            return InferenceJobStatus.COMPLETED
        except Exception:
            logger.exception("Video processing failed for job %s", job_id)
            for key in uploaded:
                self._delete_uploaded_evidence(key)
            self._mark_failed(job_id)
            return InferenceJobStatus.FAILED

    def _geolocate(
        self, parsed: ParsedInference, result: Mapping[str, Any], source: ClaimedJob
    ) -> ParsedInference:
        """Enrich model boxes before persistence, with no database session open."""
        width, height = result.get("image_width"), result.get("image_height")
        if type(width) is not int or type(height) is not int or min(width, height) <= 0:
            raise InferenceOutputError("Prediction dimensions must be positive integers")
        detections = [
            replace(
                detection,
                location=geolocate_detection(
                    detection.bounding_box, width, height,
                    latitude=source.latitude, longitude=source.longitude,
                    heading_deg=self._heading_deg, profile=self._camera_profile,
                ),
            )
            for detection in parsed.detections
        ]
        return replace(parsed, detections=detections)

    def _claim(self, job_id: UUID) -> ClaimedJob | None:
        with self._session_factory() as session, session.begin():
            jobs = InferenceJobRepository(session)
            job = jobs.get_for_update(job_id)
            if job is None:
                logger.error("Inference job %s does not exist; skipping", job_id)
                return None
            if job.status != InferenceJobStatus.PENDING:
                logger.warning(
                    "Inference job %s is %s, not pending; skipping", job_id, job.status
                )
                return None
            jobs.mark_running(job, started_at=self._clock())
            return ClaimedJob(
                image=job.input_media.data,
                object_storage_key=job.input_media.object_storage_key,
                filename=job.input_media.filename,
                latitude=float(job.latitude),
                longitude=float(job.longitude),
                content_type=job.input_media.content_type,
            )

    def _load_input_media(self, claimed: ClaimedJob) -> bytes:
        if claimed.object_storage_key is not None:
            return self._media_storage.get(claimed.object_storage_key)
        if claimed.image is not None:
            return claimed.image
        raise RuntimeError("Input media has no payload")

    def _save_results(
        self,
        job_id: UUID,
        parsed: ParsedInference,
        annotated_jpeg: bytes,
        detected_at: datetime,
    ) -> None:
        media_id = uuid7()
        object_storage_key = f"{EVIDENCE_MEDIA_OBJECT_KEY_PREFIX}/{media_id}"
        try:
            self._media_storage.put(
                annotated_jpeg, object_storage_key, EVIDENCE_CONTENT_TYPE
            )
        except Exception:
            self._delete_uploaded_evidence(object_storage_key)
            raise

        try:
            with self._session_factory() as session, session.begin():
                jobs = InferenceJobRepository(session)
                job = jobs.get_for_update(job_id)
                if job is None or job.status != InferenceJobStatus.RUNNING:
                    raise RuntimeError("job is no longer running")
                evidence = EvidenceMediaRepository(session).add(
                    job_id,
                    media_id=media_id,
                    object_storage_key=object_storage_key,
                    content_type=EVIDENCE_CONTENT_TYPE,
                    filename=f"annotated-{job_id}.jpg",
                )
                DetectionRecordRepository(session).add_many(
                    job_id, evidence, parsed.detections, detected_at
                )
                jobs.mark_completed(job, parsed.model_version, completed_at=self._clock())
        except Exception:
            self._delete_uploaded_evidence(object_storage_key)
            raise

    def _delete_uploaded_evidence(self, object_storage_key: str) -> None:
        try:
            self._media_storage.delete(object_storage_key)
        except Exception:
            logger.exception(
                "Could not delete orphaned evidence media %s", object_storage_key
            )

    def _mark_failed(self, job_id: UUID) -> None:
        try:
            with self._session_factory() as session, session.begin():
                InferenceJobRepository(session).mark_failed(job_id)
        except Exception:
            logger.exception("Could not mark inference job %s failed", job_id)
