from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import DetectionRecord, EvidenceMedia


@dataclass(frozen=True)
class NewDetection:
    issue_type: int
    confidence: float
    severity: str
    bounding_box: list[float]
    location: dict[str, float] | None = None
    location_status: str | None = None
    location_details: dict[str, Any] | None = None


class DetectionRecordRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add_many(
        self,
        job_id: UUID,
        evidence: EvidenceMedia,
        detections: Sequence[NewDetection],
        detected_at: datetime,
    ) -> list[DetectionRecord]:
        records = [
            DetectionRecord(
                job_id=job_id,
                evidence=evidence,
                issue_type=detection.issue_type,
                confidence=detection.confidence,
                severity=detection.severity,
                bounding_box=list(detection.bounding_box),
                location=detection.location,
                location_status=detection.location_status,
                location_details=detection.location_details,
                frame_timestamp=None,
                detected_at=detected_at,
            )
            for detection in detections
        ]
        self._session.add_all(records)
        self._session.flush()
        return records

    def get(self, detection_id: UUID) -> DetectionRecord | None:
        return self._session.get(DetectionRecord, detection_id)

    def list_for_job(
        self, job_id: UUID, offset: int, limit: int
    ) -> tuple[list[DetectionRecord], int]:
        total = self._session.scalar(
            select(func.count())
            .select_from(DetectionRecord)
            .where(DetectionRecord.job_id == job_id)
        )
        rows = self._session.scalars(
            select(DetectionRecord)
            .where(DetectionRecord.job_id == job_id)
            .order_by(
                DetectionRecord.detected_at,
                DetectionRecord.confidence.desc(),
                DetectionRecord.detection_id,
            )
            .offset(offset)
            .limit(limit)
        ).all()
        return list(rows), int(total or 0)
