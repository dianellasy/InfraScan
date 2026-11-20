from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, Double, ForeignKey, LargeBinary, String, Uuid, text
from sqlalchemy.dialects.postgresql import BYTEA
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

if TYPE_CHECKING:
    from app.models.detection_record import DetectionRecord
    from app.models.inference_job import InferenceJob

# New payloads live in object storage; `data` remains only for legacy rows.
PAYLOAD_CHECK = "data IS NOT NULL OR object_storage_key IS NOT NULL"


def _bytea():
    return LargeBinary().with_variant(BYTEA(), "postgresql")


class InputMedia(Base):
    """Submitted JPEG or MP4 referenced by one or more inference jobs."""

    __tablename__ = "input_media"
    __table_args__ = (CheckConstraint(PAYLOAD_CHECK, name="data_or_object_storage_key"),)

    media_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid4,
        server_default=text("gen_random_uuid()"),
    )
    data: Mapped[bytes | None] = mapped_column(_bytea(), nullable=True)
    content_type: Mapped[str] = mapped_column(
        String(),
        nullable=False,
    )
    filename: Mapped[str | None] = mapped_column(
        String(),
        nullable=True,
    )
    object_storage_key: Mapped[str | None] = mapped_column(String(), nullable=True)

    # `passive_deletes="all"` prevents SQLAlchemy from attempting to blank out
    # `InferenceJob.input_media_id` when deleting media, delegating enforcement
    # to the database's `ON DELETE RESTRICT` foreign key constraint.
    jobs: Mapped[list[InferenceJob]] = relationship(
        "InferenceJob",
        back_populates="input_media",
        passive_deletes="all",
    )


class EvidenceMedia(Base):
    """Annotated evidence image produced by one inference job.

    Stored separately from `InputMedia` so the original upload stays intact.
    All detections from the same processed image share one evidence row; a
    video job has one row per sampled frame, with that frame's timestamp.
    """

    __tablename__ = "evidence_media"
    __table_args__ = (
        CheckConstraint(PAYLOAD_CHECK, name="data_or_object_storage_key"),
        CheckConstraint("frame_timestamp >= 0", name="frame_timestamp_nonnegative"),
    )

    media_id: Mapped[UUID] = mapped_column(
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
    data: Mapped[bytes | None] = mapped_column(_bytea(), nullable=True)
    content_type: Mapped[str] = mapped_column(
        String(),
        nullable=False,
    )
    filename: Mapped[str | None] = mapped_column(
        String(),
        nullable=True,
    )
    object_storage_key: Mapped[str | None] = mapped_column(String(), nullable=True)
    frame_timestamp: Mapped[float | None] = mapped_column(Double(), nullable=True)

    job: Mapped[InferenceJob] = relationship(
        "InferenceJob",
        back_populates="evidence_media",
    )
    detection_records: Mapped[list[DetectionRecord]] = relationship(
        "DetectionRecord",
        back_populates="evidence",
        passive_deletes="all",
    )
