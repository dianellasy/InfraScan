"""Make media data nullable and require data or object_storage_key

Revision ID: b2d4f6a81930
Revises: a1c3e5f70829
Create Date: 2026-11-18 12:05:00.000000

Allows R2-only media rows by making `data` nullable, and rejects rows that have
neither inline bytes nor an R2 object key. Downgrade restores `data NOT NULL`
and will fail if any R2-only rows still have NULL `data`.
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "b2d4f6a81930"
down_revision: str | Sequence[str] | None = "a1c3e5f70829"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PAYLOAD_CHECK = "data IS NOT NULL OR object_storage_key IS NOT NULL"
BYTEA = sa.LargeBinary().with_variant(postgresql.BYTEA(), "postgresql")


def upgrade() -> None:
    """Allow R2-only media and enforce at least one payload reference."""
    op.alter_column(
        "input_media",
        "data",
        existing_type=BYTEA,
        nullable=True,
    )
    op.alter_column(
        "evidence_media",
        "data",
        existing_type=BYTEA,
        nullable=True,
    )
    op.create_check_constraint(
        op.f("ck_input_media_data_or_object_storage_key"),
        "input_media",
        PAYLOAD_CHECK,
    )
    op.create_check_constraint(
        op.f("ck_evidence_media_data_or_object_storage_key"),
        "evidence_media",
        PAYLOAD_CHECK,
    )


def downgrade() -> None:
    """Drop payload checks and restore required `data` columns.

    Fails if any row has NULL `data` (for example R2-only media), which is the
    intended guard against invalidating those rows on downgrade.
    """
    op.drop_constraint(
        op.f("ck_evidence_media_data_or_object_storage_key"),
        "evidence_media",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_input_media_data_or_object_storage_key"),
        "input_media",
        type_="check",
    )
    op.alter_column(
        "evidence_media",
        "data",
        existing_type=BYTEA,
        nullable=False,
    )
    op.alter_column(
        "input_media",
        "data",
        existing_type=BYTEA,
        nullable=False,
    )
