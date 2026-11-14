"""Add detection geolocation fields

Revision ID: c3f8a2d6e915
Revises: 9e2b6d4f1a37
Create Date: 2026-11-13 16:00:00.000000

Adds job-level geolocation provenance (`inference_jobs.geolocation`) and
per-detection projection outcome (`detection_records.location_status` and
`location_details`). Existing rows keep NULL, meaning geolocation was not
attempted.
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "c3f8a2d6e915"
down_revision: str | Sequence[str] | None = "9e2b6d4f1a37"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

LOCATION_STATUS_CHECK = (
    "location_status IS NULL OR location_status IN ("
    "'estimated', 'above_horizon', 'behind_camera', 'unstable_near_horizon', "
    "'outside_image', 'nonfinite', 'undistortion_failed', 'intrinsics_mismatch')"
)


def _jsonb() -> sa.types.TypeEngine:
    return sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.add_column(
        "inference_jobs",
        sa.Column("geolocation", _jsonb(), nullable=True),
    )
    op.add_column(
        "detection_records",
        sa.Column("location_status", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "detection_records",
        sa.Column("location_details", _jsonb(), nullable=True),
    )
    op.create_check_constraint(
        op.f("ck_detection_records_location_status_allowed"),
        "detection_records",
        LOCATION_STATUS_CHECK,
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("ck_detection_records_location_status_allowed"),
        "detection_records",
        type_="check",
    )
    op.drop_column("detection_records", "location_details")
    op.drop_column("detection_records", "location_status")
    op.drop_column("inference_jobs", "geolocation")
