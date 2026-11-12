from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import LargeBinary, String, Uuid, text
from sqlalchemy.dialects.postgresql import BYTEA
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

if TYPE_CHECKING:
    from app.models.inference_job import InferenceJob


class InputMedia(Base):
    """Persisted input image payload referenced by one or more inference jobs."""

    __tablename__ = "input_media"

    media_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid4,
        server_default=text("gen_random_uuid()"),
    )
    data: Mapped[bytes] = mapped_column(
        LargeBinary().with_variant(BYTEA(), "postgresql"),
        nullable=False,
    )
    content_type: Mapped[str] = mapped_column(
        String(),
        nullable=False,
    )
    filename: Mapped[str | None] = mapped_column(
        String(),
        nullable=True,
    )

    # `passive_deletes="all"` prevents SQLAlchemy from attempting to blank out
    # `InferenceJob.input_media_id` when deleting media, delegating enforcement
    # to the database's `ON DELETE RESTRICT` foreign key constraint.
    jobs: Mapped[list[InferenceJob]] = relationship(
        "InferenceJob",
        back_populates="input_media",
        passive_deletes="all",
    )
