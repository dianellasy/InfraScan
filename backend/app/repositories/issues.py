from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import ColumnElement, Select, and_, case, func, or_, select
from sqlalchemy.orm import Session

from app.models import (
    DetectionRecord,
    EvidenceMedia,
    Issue,
    IssueStatus,
    IssueType,
    issue_point,
)
from app.schemas import BoundingBox, IssueFilters


@dataclass(frozen=True)
class IssueRow:
    issue: Issue
    issue_type_name: str
    detection_count: int
    evidence_media_id: UUID | None


_SEVERITY_RANK = case(
    {"critical": 0, "moderate": 1, "minor": 2}, value=Issue.severity, else_=3
)
_ORDER_BY = (_SEVERITY_RANK, Issue.last_seen_at.desc(), Issue.issue_id)


def _detection_count() -> ColumnElement[int]:
    return (
        select(func.count())
        .where(DetectionRecord.issue_id == Issue.issue_id)
        .correlate(Issue)
        .scalar_subquery()
    )


def _representative_evidence() -> ColumnElement[UUID]:
    return (
        select(DetectionRecord.evidence_media)
        .where(
            DetectionRecord.issue_id == Issue.issue_id,
            DetectionRecord.evidence_media.is_not(None),
        )
        .order_by(
            DetectionRecord.confidence.desc(),
            DetectionRecord.detected_at.desc(),
            DetectionRecord.detection_id,
        )
        .limit(1)
        .correlate(Issue)
        .scalar_subquery()
    )


def needs_review_clause() -> ColumnElement[bool]:
    return or_(
        Issue.status == IssueStatus.PENDING_REVIEW.value,
        Issue.review_reason.is_not(None),
    )


def _filter_clauses(filters: IssueFilters) -> list[ColumnElement[bool]]:
    clauses: list[ColumnElement[bool]] = []
    if filters.issue_type:
        clauses.append(Issue.issue_type_code.in_([t.value for t in filters.issue_type]))
    if filters.severity:
        clauses.append(Issue.severity.in_([s.value for s in filters.severity]))
    if filters.status:
        clauses.append(Issue.status.in_([s.value for s in filters.status]))
    if filters.review_required:
        clauses.append(needs_review_clause())
    if filters.last_seen_after is not None:
        clauses.append(Issue.last_seen_at >= filters.last_seen_after)
    if filters.last_seen_before is not None:
        clauses.append(Issue.last_seen_at <= filters.last_seen_before)
    return clauses


def _bbox_clause(bbox: BoundingBox) -> ColumnElement[bool]:
    # Matches the partial GiST index `ix_issues_location` (see app.models.issue).
    viewport = func.box(
        func.point(bbox.min_longitude, bbox.min_latitude),
        func.point(bbox.max_longitude, bbox.max_latitude),
    )
    return and_(
        Issue.latitude.is_not(None),
        Issue.longitude.is_not(None),
        issue_point().op("<@", is_comparison=True)(viewport),
    )


def _rows_query() -> Select:
    return select(
        Issue,
        IssueType.display_name,
        _detection_count().label("detection_count"),
        _representative_evidence().label("evidence_media_id"),
    ).join(IssueType, IssueType.code == Issue.issue_type_code)


def _to_row(row) -> IssueRow:
    issue, display_name, detection_count, evidence_media_id = row
    return IssueRow(issue, display_name, int(detection_count), evidence_media_id)


class IssueRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def _count(self, clauses: list[ColumnElement[bool]]) -> int:
        total = self._session.scalar(
            select(func.count()).select_from(Issue).where(*clauses)
        )
        return int(total or 0)

    def _list(
        self, clauses: list[ColumnElement[bool]], offset: int, limit: int
    ) -> list[IssueRow]:
        rows = self._session.execute(
            _rows_query().where(*clauses).order_by(*_ORDER_BY).offset(offset).limit(limit)
        ).all()
        return [_to_row(row) for row in rows]

    def list_in_bbox(
        self, bbox: BoundingBox, filters: IssueFilters, limit: int
    ) -> tuple[list[IssueRow], int]:
        """Located Issues inside `bbox`, most severe and most recent first."""
        clauses = [_bbox_clause(bbox), *_filter_clauses(filters)]
        return self._list(clauses, 0, limit), self._count(clauses)

    def list_filtered(
        self,
        filters: IssueFilters,
        bbox: BoundingBox | None,
        offset: int,
        limit: int,
    ) -> tuple[list[IssueRow], int]:
        clauses = _filter_clauses(filters)
        if bbox is not None:
            clauses.append(_bbox_clause(bbox))
        return self._list(clauses, offset, limit), self._count(clauses)

    def list_review_queue(self, offset: int, limit: int) -> tuple[list[IssueRow], int]:
        """Issues needing review, including those without coordinates."""
        clauses = [needs_review_clause()]
        return self._list(clauses, offset, limit), self._count(clauses)

    def get(self, issue_id: UUID) -> IssueRow | None:
        row = self._session.execute(
            _rows_query().where(Issue.issue_id == issue_id)
        ).one_or_none()
        return _to_row(row) if row is not None else None

    def exists(self, issue_id: UUID) -> bool:
        return self._session.get(Issue, issue_id) is not None

    def list_detections(
        self, issue_id: UUID, offset: int, limit: int
    ) -> tuple[list[DetectionRecord], int]:
        where = DetectionRecord.issue_id == issue_id
        total = self._session.scalar(
            select(func.count()).select_from(DetectionRecord).where(where)
        )
        rows = self._session.scalars(
            select(DetectionRecord)
            .where(where)
            .order_by(
                DetectionRecord.detected_at,
                DetectionRecord.confidence.desc(),
                DetectionRecord.detection_id,
            )
            .offset(offset)
            .limit(limit)
        ).all()
        return list(rows), int(total or 0)

    def get_evidence(self, media_id: UUID) -> EvidenceMedia | None:
        return self._session.get(EvidenceMedia, media_id)
