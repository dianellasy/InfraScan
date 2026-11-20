"""Record when each detection's source image or frame was captured

Revision ID: d4f6a8b03152
Revises: c3e5f7a92041
Create Date: 2026-11-20 14:00:00.000000

`detection_records.observed_at` is the capture time (UTC) of the image or
video frame a detection came from; `detected_at` keeps meaning "inference
finished". Existing still-image rows (`frame_timestamp IS NULL`) are filled
from their job's `captured_at`, which is exactly what the worker now writes.
Existing video rows stay NULL: their frame times depend on the clip's GPS
timeline, which is not stored in PostgreSQL.
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "d4f6a8b03152"
down_revision: str | Sequence[str] | None = "c3e5f7a92041"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "detection_records",
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute(
        "UPDATE detection_records AS d SET observed_at = j.captured_at "
        "FROM inference_jobs AS j "
        "WHERE d.job_id = j.job_id AND d.frame_timestamp IS NULL"
    )


def downgrade() -> None:
    op.drop_column("detection_records", "observed_at")
