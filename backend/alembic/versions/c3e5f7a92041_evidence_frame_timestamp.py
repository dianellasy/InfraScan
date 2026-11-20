"""Track the source-frame timestamp of video evidence

Revision ID: c3e5f7a92041
Revises: b2d4f6a81930
Create Date: 2026-11-20 12:10:00.000000

`frame_timestamp` is the normalized PTS in seconds of the sampled video frame
an evidence image came from; it is NULL for image submissions.
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "c3e5f7a92041"
down_revision: str | Sequence[str] | None = "b2d4f6a81930"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "evidence_media", sa.Column("frame_timestamp", sa.Double(), nullable=True)
    )
    op.create_check_constraint(
        op.f("ck_evidence_media_frame_timestamp_nonnegative"),
        "evidence_media",
        "frame_timestamp >= 0",
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("ck_evidence_media_frame_timestamp_nonnegative"),
        "evidence_media",
        type_="check",
    )
    op.drop_column("evidence_media", "frame_timestamp")
