"""Track video evidence frame timestamps.

Revision ID: c3e5f7a92041
Revises: b2d4f6a81930
"""
from alembic import op
import sqlalchemy as sa

revision = "c3e5f7a92041"
down_revision = "b2d4f6a81930"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("evidence_media", sa.Column("frame_timestamp", sa.Double(), nullable=True))
    op.create_check_constraint(op.f("ck_evidence_media_frame_timestamp_nonnegative"),
                               "evidence_media", "frame_timestamp >= 0")


def downgrade():
    op.drop_constraint(op.f("ck_evidence_media_frame_timestamp_nonnegative"), "evidence_media", type_="check")
    op.drop_column("evidence_media", "frame_timestamp")
