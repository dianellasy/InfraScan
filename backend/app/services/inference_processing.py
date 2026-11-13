from __future__ import annotations

import logging
import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from app.models import DETECTION_SEVERITIES, InferenceJobStatus
from app.repositories import (
    DetectionRecordRepository,
    EvidenceMediaRepository,
    InferenceJobRepository,
    NewDetection,
)

logger = logging.getLogger(__name__)

EVIDENCE_CONTENT_TYPE = "image/jpeg"

# (image bytes, filename) -> (v0-mvp result JSON, annotated JPEG bytes)
InferenceRunner = Callable[[bytes, str | None], tuple[Mapping[str, Any], bytes]]


class InferenceOutputError(ValueError):
    """The pipeline returned output that does not match the v0-mvp contract."""


@dataclass(frozen=True)
class ParsedInference:
    model_version: str
    detections: list[NewDetection]


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

    Three short transactions: claim the pending job (mark it running), then
    run inference with no session open, then save the evidence image, all
    detections, and the completed status together. Ordinary failures mark the
    job failed. Retries, duplicate-delivery guarantees, and recovery of
    stranded jobs are deferred until after the MVP.
    """

    def __init__(
        self,
        session_factory: Callable[[], Session],
        run_inference: InferenceRunner,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._session_factory = session_factory
        self._run_inference = run_inference
        self._clock = clock

    def process(self, job_id: UUID) -> InferenceJobStatus | None:
        """Return the job's final status, or None if this call did not run it."""
        try:
            claimed = self._claim(job_id)
        except Exception:
            logger.exception("Could not start inference job %s", job_id)
            raise
        if claimed is None:
            return None
        image, filename = claimed

        try:
            result, annotated_jpeg = self._run_inference(image, filename)
            detected_at = self._clock()
            parsed = parse_inference_output(result)
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

    def _claim(self, job_id: UUID) -> tuple[bytes, str | None] | None:
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
            return job.input_media.data, job.input_media.filename

    def _save_results(
        self,
        job_id: UUID,
        parsed: ParsedInference,
        annotated_jpeg: bytes,
        detected_at: datetime,
    ) -> None:
        with self._session_factory() as session, session.begin():
            jobs = InferenceJobRepository(session)
            job = jobs.get_for_update(job_id)
            if job is None or job.status != InferenceJobStatus.RUNNING:
                raise RuntimeError("job is no longer running")
            evidence = EvidenceMediaRepository(session).add(
                job_id,
                data=annotated_jpeg,
                content_type=EVIDENCE_CONTENT_TYPE,
                filename=f"annotated-{job_id}.jpg",
            )
            DetectionRecordRepository(session).add_many(
                job_id, evidence, parsed.detections, detected_at
            )
            jobs.mark_completed(job, parsed.model_version, completed_at=self._clock())

    def _mark_failed(self, job_id: UUID) -> None:
        try:
            with self._session_factory() as session, session.begin():
                InferenceJobRepository(session).mark_failed(job_id)
        except Exception:
            logger.exception("Could not mark inference job %s failed", job_id)
