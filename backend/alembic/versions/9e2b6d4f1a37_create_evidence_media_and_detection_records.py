"""Create evidence_media and detection_records tables

Revision ID: 9e2b6d4f1a37
Revises: 7c4e9a1b2d01
Create Date: 2026-11-12 17:00:00.000000

`detection_records.issue_type` provisionally stores the v0-mvp model class ID
(0 Longitudinal crack, 1 Transverse crack, 2 Alligator crack, 3 Pothole). The
foreign keys for `issue_id` and `issue_type` are intentionally deferred because
the `issues` and `issue_types` tables do not exist yet. The later issue-types
migration must define the class-ID mapping and convert stored values.
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "9e2b6d4f1a37"
down_revision: str | Sequence[str] | None = "7c4e9a1b2d01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

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


def upgrade() -> None:
    """Create annotated-evidence and detection persistence tables."""
    op.create_table(
        "evidence_media",
        sa.Column(
            "media_id",
            sa.Uuid(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column(
            "data",
            sa.LargeBinary().with_variant(postgresql.BYTEA(), "postgresql"),
            nullable=False,
        ),
        sa.Column("content_type", sa.String(), nullable=False),
        sa.Column("filename", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["inference_jobs.job_id"],
            name=op.f("fk_evidence_media_job_id_inference_jobs"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("media_id", name=op.f("pk_evidence_media")),
    )
    op.create_index(
        op.f("ix_evidence_media_job_id"),
        "evidence_media",
        ["job_id"],
        unique=False,
    )

    op.create_table(
        "detection_records",
        sa.Column(
            "detection_id",
            sa.Uuid(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("issue_id", sa.Uuid(), nullable=True),
        sa.Column("issue_type", sa.Integer(), nullable=False),
        sa.Column("evidence_media", sa.Uuid(), nullable=True),
        sa.Column(
            "location",
            sa.JSON().with_variant(postgresql.JSONB(), "postgresql"),
            nullable=True,
        ),
        sa.Column("confidence", sa.Double(), nullable=False),
        sa.Column("severity", sa.String(length=16), nullable=False),
        sa.Column(
            "bounding_box",
            sa.JSON().with_variant(postgresql.JSONB(), "postgresql"),
            nullable=False,
        ),
        sa.Column("frame_timestamp", sa.Double(), nullable=True),
        sa.Column("detected_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "confidence >= 0 AND confidence <= 1",
            name=op.f("ck_detection_records_confidence_range"),
        ),
        sa.CheckConstraint(
            "severity IN ('minor', 'moderate', 'critical')",
            name=op.f("ck_detection_records_severity_allowed"),
        ),
        sa.CheckConstraint(
            "frame_timestamp >= 0",
            name=op.f("ck_detection_records_frame_timestamp_nonnegative"),
        ),
        sa.CheckConstraint(
            BOUNDING_BOX_CHECK,
            name=op.f("ck_detection_records_bounding_box_xyxy"),
        ),
        sa.CheckConstraint(
            LOCATION_CHECK,
            name=op.f("ck_detection_records_location_lat_lon"),
        ),
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["inference_jobs.job_id"],
            name=op.f("fk_detection_records_job_id_inference_jobs"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["evidence_media"],
            ["evidence_media.media_id"],
            name=op.f("fk_detection_records_evidence_media_evidence_media"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "detection_id", name=op.f("pk_detection_records")
        ),
    )
    op.create_index(
        op.f("ix_detection_records_job_id"),
        "detection_records",
        ["job_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_detection_records_evidence_media"),
        "detection_records",
        ["evidence_media"],
        unique=False,
    )


def downgrade() -> None:
    """Remove only the objects created by this revision in reverse FK order."""
    op.drop_index(
        op.f("ix_detection_records_evidence_media"),
        table_name="detection_records",
    )
    op.drop_index(
        op.f("ix_detection_records_job_id"),
        table_name="detection_records",
    )
    op.drop_table("detection_records")
    op.drop_index(
        op.f("ix_evidence_media_job_id"),
        table_name="evidence_media",
    )
    op.drop_table("evidence_media")
