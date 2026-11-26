from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import ColumnElement, func, select
from sqlalchemy.orm import Session

from app.models import DetectionRecord, EvidenceMedia, Issue


@dataclass(frozen=True)
class NewDetection:
    issue_type: int
    confidence: float
    severity: str
    bounding_box: list[float]
    location: dict[str, float] | None = None


class DetectionRecordRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add_many(
        self,
        job_id: UUID,
        evidence: EvidenceMedia,
        detections: Sequence[NewDetection],
        detected_at: datetime,
        observed_at: datetime | None = None,
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
                # Video detections carry their source frame's PTS; images have none.
                frame_timestamp=evidence.frame_timestamp,
                detected_at=detected_at,
                observed_at=observed_at,
            )
            for detection in detections
        ]
        self._session.add_all(records)
        self._session.flush()
        return records

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
                DetectionRecord.frame_timestamp.asc().nulls_first(),
                DetectionRecord.detected_at,
                DetectionRecord.confidence.desc(),
                DetectionRecord.detection_id,
            )
            .offset(offset)
            .limit(limit)
        ).all()
        return list(rows), int(total or 0)


@dataclass(frozen=True)
class DetectionFilters:
    """Filters for the global detection register (all optional)."""

    class_id: int | None = None
    severity: str | None = None
    confidence_min: float | None = None
    confidence_max: float | None = None
    detected_after: datetime | None = None
    detected_before: datetime | None = None
    min_latitude: float | None = None
    min_longitude: float | None = None
    max_latitude: float | None = None
    max_longitude: float | None = None


def _filter_clauses(filters: DetectionFilters) -> list[ColumnElement[bool]]:
    clauses: list[ColumnElement[bool]] = []
    if filters.class_id is not None:
        clauses.append(DetectionRecord.issue_type == filters.class_id)
    if filters.severity is not None:
        clauses.append(DetectionRecord.severity == filters.severity)
    if filters.confidence_min is not None:
        clauses.append(DetectionRecord.confidence >= filters.confidence_min)
    if filters.confidence_max is not None:
        clauses.append(DetectionRecord.confidence <= filters.confidence_max)
    if filters.detected_after is not None:
        clauses.append(DetectionRecord.detected_at >= filters.detected_after)
    if filters.detected_before is not None:
        clauses.append(DetectionRecord.detected_at <= filters.detected_before)
    latitude = DetectionRecord.location["latitude"].as_float()
    longitude = DetectionRecord.location["longitude"].as_float()
    if filters.min_latitude is not None:
        clauses.append(latitude >= filters.min_latitude)
    if filters.max_latitude is not None:
        clauses.append(latitude <= filters.max_latitude)
    if filters.min_longitude is not None:
        clauses.append(longitude >= filters.min_longitude)
    if filters.max_longitude is not None:
        clauses.append(longitude <= filters.max_longitude)
    return clauses


class DetectionRegisterRepository:
    """Detections across every job, each with its Issue's review status.

    Not owner-scoped: only privileged callers may use it (see the route).
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    def list(
        self, filters: DetectionFilters, offset: int, limit: int
    ) -> tuple[list[tuple[DetectionRecord, str | None]], int]:
        clauses = _filter_clauses(filters)
        total = self._session.scalar(
            select(func.count()).select_from(DetectionRecord).where(*clauses)
        )
        rows = self._session.execute(
            select(DetectionRecord, Issue.status)
            .outerjoin(Issue, Issue.issue_id == DetectionRecord.issue_id)
            .where(*clauses)
            .order_by(
                DetectionRecord.detected_at.desc(),
                DetectionRecord.confidence.desc(),
                DetectionRecord.detection_id,
            )
            .offset(offset)
            .limit(limit)
        ).all()
        return [(record, status) for record, status in rows], int(total or 0)

    def get(self, detection_id: UUID) -> tuple[DetectionRecord, str | None] | None:
        row = self._session.execute(
            select(DetectionRecord, Issue.status)
            .outerjoin(Issue, Issue.issue_id == DetectionRecord.issue_id)
            .where(DetectionRecord.detection_id == detection_id)
        ).first()
        return (row[0], row[1]) if row is not None else None
