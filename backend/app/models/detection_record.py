from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    Double,
    ForeignKey,
    Integer,
    String,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

if TYPE_CHECKING:
    from app.models.inference_job import InferenceJob
    from app.models.media import EvidenceMedia


class DetectionSeverity(StrEnum):
    """Severity values produced by the v0-mvp heuristic (not a learned output)."""

    MINOR = "minor"
    MODERATE = "moderate"
    CRITICAL = "critical"


DETECTION_SEVERITIES: tuple[str, ...] = tuple(s.value for s in DetectionSeverity)

# CASE guards keep PostgreSQL from evaluating array/object accessors on values
# of the wrong JSON type, and avoid NULL (which a CHECK treats as passing).
BOUNDING_BOX_CHECK = (
    "CASE WHEN jsonb_typeof(bounding_box) = 'array' "
    "THEN jsonb_array_length(bounding_box) = 4 "
    "AND jsonb_typeof(bounding_box -> 0) = 'number' "
    "AND jsonb_typeof(bounding_box -> 1) = 'number' "
    "AND jsonb_typeof(bounding_box -> 2) = 'number' "
    "AND jsonb_typeof(bounding_box -> 3) = 'number' "
    "ELSE false END"
)
LOCATION_CHECK = (
    "location IS NULL OR CASE "
    "WHEN jsonb_typeof(location) = 'object' "
    "AND jsonb_typeof(location -> 'latitude') IS NOT DISTINCT FROM 'number' "
    "AND jsonb_typeof(location -> 'longitude') IS NOT DISTINCT FROM 'number' "
    "THEN (location ->> 'latitude')::numeric BETWEEN -90 AND 90 "
    "AND (location ->> 'longitude')::numeric BETWEEN -180 AND 180 "
    "ELSE false END"
)


def _jsonb() -> JSON:
    # Persist Python None as SQL NULL rather than the JSON literal `null`.
    return JSON(none_as_null=True).with_variant(JSONB(none_as_null=True), "postgresql")


class DetectionRecord(Base):
    """One road-damage detection produced by an inference job.

    `issue_type` provisionally stores the v0-mvp model class ID
    (see `inference/labels.py`), not a permanent domain identifier. The
    diagram's foreign keys for `issue_id` and `issue_type` are deferred until
    the `issues` and `issue_types` tables exist; that later migration must
    define the class-ID mapping and convert stored values.

    `bounding_box` is `[x1, y1, x2, y2]` in pixels of the prediction source
    image at its original dimensions (not normalized YOLO coordinates and not
    a fixed 640x640 grid). `location` stays NULL until detection-level GPS is
    calculated; capture coordinates remain on `InferenceJob`.
    """

    __tablename__ = "detection_records"
    __table_args__ = (
        CheckConstraint(
            "confidence >= 0 AND confidence <= 1",
            name="confidence_range",
        ),
        CheckConstraint(
            "severity IN ('minor', 'moderate', 'critical')",
            name="severity_allowed",
        ),
        CheckConstraint(
            "frame_timestamp >= 0",
            name="frame_timestamp_nonnegative",
        ),
        CheckConstraint(BOUNDING_BOX_CHECK, name="bounding_box_xyxy"),
        CheckConstraint(LOCATION_CHECK, name="location_lat_lon"),
    )

    detection_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid4,
        server_default=text("gen_random_uuid()"),
    )
    job_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("inference_jobs.job_id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    issue_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        nullable=True,
    )
    issue_type: Mapped[int] = mapped_column(
        Integer(),
        nullable=False,
    )
    evidence_media: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("evidence_media.media_id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    location: Mapped[dict[str, Any] | None] = mapped_column(
        _jsonb(),
        nullable=True,
    )
    confidence: Mapped[float] = mapped_column(
        Double(),
        nullable=False,
    )
    severity: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
    )
    bounding_box: Mapped[list[float]] = mapped_column(
        _jsonb(),
        nullable=False,
    )
    frame_timestamp: Mapped[float | None] = mapped_column(
        Double(),
        nullable=True,
    )
    detected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    job: Mapped[InferenceJob] = relationship(
        "InferenceJob",
        back_populates="detection_records",
    )
    evidence: Mapped[EvidenceMedia | None] = relationship(
        "EvidenceMedia",
        back_populates="detection_records",
    )
