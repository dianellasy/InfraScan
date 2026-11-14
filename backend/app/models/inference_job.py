from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Numeric,
    String,
    Uuid,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

if TYPE_CHECKING:
    from app.models.detection_record import DetectionRecord
    from app.models.media import EvidenceMedia, InputMedia


class InferenceJobStatus(StrEnum):
    """Allowed lifecycle states for persisted inference jobs."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


INFERENCE_JOB_STATUSES: tuple[str, ...] = tuple(
    status.value for status in InferenceJobStatus
)


class InferenceJob(Base):
    """Application-level road-damage inference job persisted in PostgreSQL.

    API contract mapping note:
    The public HTTP DTOs (`InferenceJobAccepted` and `InferenceJobDTO`) expose
    the submission timestamp as `submitted_at` (and filter query parameters as
    `submitted_after` / `submitted_before`). Persistence stores this column as
    `created_at`. Future service and repository implementations MUST map
    `InferenceJob.created_at` <-> `submitted_at` to preserve the API contract.
    """

    __tablename__ = "inference_jobs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'running', 'completed', 'failed')",
            name="status_allowed",
        ),
        CheckConstraint(
            "latitude >= -90 AND latitude <= 90",
            name="latitude_range",
        ),
        CheckConstraint(
            "longitude >= -180 AND longitude <= 180",
            name="longitude_range",
        ),
    )

    job_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid4,
        server_default=text("gen_random_uuid()"),
    )
    # Authentication is deferred and no `users` table exists yet; keep nullable
    # without inventing a foreign key target until auth persistence lands.
    user_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        nullable=True,
    )
    input_media_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("input_media.media_id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=InferenceJobStatus.PENDING.value,
        server_default=text("'pending'"),
    )
    # Left NULL until the application/worker assigns a concrete model version.
    model_version: Mapped[str | None] = mapped_column(
        String(),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    latitude: Mapped[Decimal] = mapped_column(
        Numeric(),
        nullable=False,
    )
    longitude: Mapped[Decimal] = mapped_column(
        Numeric(),
        nullable=False,
    )

    input_media: Mapped[InputMedia] = relationship(
        "InputMedia",
        back_populates="jobs",
    )
    # Children use `ON DELETE RESTRICT`; `passive_deletes="all"` leaves that
    # enforcement to PostgreSQL instead of nulling their required `job_id`.
    detection_records: Mapped[list[DetectionRecord]] = relationship(
        "DetectionRecord",
        back_populates="job",
        passive_deletes="all",
    )
    evidence_media: Mapped[list[EvidenceMedia]] = relationship(
        "EvidenceMedia",
        back_populates="job",
        passive_deletes="all",
    )
