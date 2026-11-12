"""Create input_media and inference_jobs tables

Revision ID: 7c4e9a1b2d01
Revises:
Create Date: 2026-11-11 18:10:00.000000

"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "7c4e9a1b2d01"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create application media and inference-job persistence tables."""
    op.create_table(
        "input_media",
        sa.Column(
            "media_id",
            sa.Uuid(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "data",
            sa.LargeBinary().with_variant(postgresql.BYTEA(), "postgresql"),
            nullable=False,
        ),
        sa.Column("content_type", sa.String(), nullable=False),
        sa.Column("filename", sa.String(), nullable=True),
        sa.PrimaryKeyConstraint("media_id", name=op.f("pk_input_media")),
    )

    op.create_table(
        "inference_jobs",
        sa.Column(
            "job_id",
            sa.Uuid(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("input_media_id", sa.Uuid(), nullable=False),
        sa.Column(
            "status",
            sa.String(length=32),
            server_default=sa.text("'pending'"),
            nullable=False,
        ),
        sa.Column("model_version", sa.String(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("latitude", sa.Numeric(), nullable=False),
        sa.Column("longitude", sa.Numeric(), nullable=False),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'completed', 'failed')",
            name=op.f("ck_inference_jobs_status_allowed"),
        ),
        sa.CheckConstraint(
            "latitude >= -90 AND latitude <= 90",
            name=op.f("ck_inference_jobs_latitude_range"),
        ),
        sa.CheckConstraint(
            "longitude >= -180 AND longitude <= 180",
            name=op.f("ck_inference_jobs_longitude_range"),
        ),
        sa.ForeignKeyConstraint(
            ["input_media_id"],
            ["input_media.media_id"],
            name=op.f("fk_inference_jobs_input_media_id_input_media"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("job_id", name=op.f("pk_inference_jobs")),
    )
    op.create_index(
        op.f("ix_inference_jobs_input_media_id"),
        "inference_jobs",
        ["input_media_id"],
        unique=False,
    )


def downgrade() -> None:
    """Remove only the objects created by this revision in reverse FK order."""
    op.drop_index(
        op.f("ix_inference_jobs_input_media_id"),
        table_name="inference_jobs",
    )
    op.drop_table("inference_jobs")
    op.drop_table("input_media")
