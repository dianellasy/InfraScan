"""Add nullable object_storage_key to input_media and evidence_media

Revision ID: a1c3e5f70829
Revises: 9e2b6d4f1a37
Create Date: 2026-11-18 12:00:00.000000

Adds a nullable string column that references an R2 object key for each media
row. Existing BYTEA `data` columns remain required until a later migration
moves payloads fully to object storage.
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "a1c3e5f70829"
down_revision: str | Sequence[str] | None = "9e2b6d4f1a37"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add nullable R2 object-key columns on both media tables."""
    op.add_column(
        "input_media",
        sa.Column("object_storage_key", sa.String(), nullable=True),
    )
    op.add_column(
        "evidence_media",
        sa.Column("object_storage_key", sa.String(), nullable=True),
    )


def downgrade() -> None:
    """Remove R2 object-key columns from both media tables."""
    op.drop_column("evidence_media", "object_storage_key")
    op.drop_column("input_media", "object_storage_key")
