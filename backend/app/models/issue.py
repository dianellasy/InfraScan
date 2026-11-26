from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    ColumnElement,
    DateTime,
    Double,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    Uuid,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

if TYPE_CHECKING:
    from app.models.detection_record import DetectionRecord


class IssueTypeCode(StrEnum):
    """Canonical issue types. Rows are inserted by the issue-types migration."""

    LONGITUDINAL_CRACK = "longitudinal_crack"
    TRANSVERSE_CRACK = "transverse_crack"
    ALLIGATOR_CRACK = "alligator_crack"
    POTHOLE = "pothole"


ISSUE_TYPE_DISPLAY_NAMES: dict[IssueTypeCode, str] = {
    IssueTypeCode.LONGITUDINAL_CRACK: "Longitudinal crack",
    IssueTypeCode.TRANSVERSE_CRACK: "Transverse crack",
    IssueTypeCode.ALLIGATOR_CRACK: "Alligator crack",
    IssueTypeCode.POTHOLE: "Pothole",
}

# `DetectionRecord.issue_type` keeps the raw v0-mvp model class ID (see
# `inference/labels.py`); this maps it to the canonical type it predicts.
MODEL_CLASS_ISSUE_TYPES: dict[int, IssueTypeCode] = {
    0: IssueTypeCode.LONGITUDINAL_CRACK,
    1: IssueTypeCode.TRANSVERSE_CRACK,
    2: IssueTypeCode.ALLIGATOR_CRACK,
    3: IssueTypeCode.POTHOLE,
}


class IssueStatus(StrEnum):
    PENDING_REVIEW = "pending_review"
    ACCEPTED = "accepted"
    RESOLVED = "resolved"
    DISMISSED = "dismissed"


class IssueReviewReason(StrEnum):
    TYPE_CONFLICT = "type_conflict"
    MISSING_LOCATION = "missing_location"
    LOW_CONFIDENCE = "low_confidence"


ISSUE_STATUSES: tuple[str, ...] = tuple(s.value for s in IssueStatus)
ISSUE_REVIEW_REASONS: tuple[str, ...] = tuple(r.value for r in IssueReviewReason)


def _in_list(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


class IssueType(Base):
    __tablename__ = "issue_types"

    code: Mapped[str] = mapped_column(String(32), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(64), nullable=False)
    model_class_id: Mapped[int | None] = mapped_column(
        Integer(), nullable=True, unique=True
    )


class Issue(Base):
    """One canonical road issue, supported by one or more detections.

    Coordinates belong to the Issue itself (not to any single detection) and
    are NULL when no supporting detection could be located; such Issues are
    kept for the review queue but excluded from map queries.
    """

    __tablename__ = "issues"
    __table_args__ = (
        CheckConstraint(
            "severity IN ('minor', 'moderate', 'critical')",
            name="severity_allowed",
        ),
        CheckConstraint(_in_list("status", ISSUE_STATUSES), name="status_allowed"),
        CheckConstraint(
            f"review_reason IS NULL OR {_in_list('review_reason', ISSUE_REVIEW_REASONS)}",
            name="review_reason_allowed",
        ),
        CheckConstraint(
            "(latitude IS NULL) = (longitude IS NULL)",
            name="coordinates_paired",
        ),
        CheckConstraint("latitude >= -90 AND latitude <= 90", name="latitude_range"),
        CheckConstraint(
            "longitude >= -180 AND longitude <= 180", name="longitude_range"
        ),
        CheckConstraint(
            "location_accuracy_m >= 0", name="location_accuracy_nonnegative"
        ),
        CheckConstraint("last_seen_at >= first_seen_at", name="seen_order"),
    )

    issue_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid4,
        server_default=text("gen_random_uuid()"),
    )
    issue_type_code: Mapped[str] = mapped_column(
        String(32),
        ForeignKey("issue_types.code", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    review_reason: Mapped[str | None] = mapped_column(String(32), nullable=True)
    latitude: Mapped[float | None] = mapped_column(Double(), nullable=True)
    longitude: Mapped[float | None] = mapped_column(Double(), nullable=True)
    location_accuracy_m: Mapped[float | None] = mapped_column(Double(), nullable=True)
    # Human review decision (PATCH /api/v1/issues/{issue_id}).
    review_note: Mapped[str | None] = mapped_column(Text(), nullable=True)
    reviewed_by: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    issue_type: Mapped[IssueType] = relationship("IssueType")
    detections: Mapped[list[DetectionRecord]] = relationship(
        "DetectionRecord",
        back_populates="issue",
        passive_deletes="all",
    )


def issue_point() -> ColumnElement[Any]:
    """PostgreSQL `point(x, y)` is ordered (longitude, latitude)."""
    return func.point(Issue.longitude, Issue.latitude)


# Viewport queries must filter with `issue_point() <@ box(...)` plus both
# IS NOT NULL predicates, or PostgreSQL cannot use this partial GiST index.
Index(
    "ix_issues_location",
    issue_point(),
    postgresql_using="gist",
    postgresql_where=Issue.latitude.is_not(None) & Issue.longitude.is_not(None),
)
