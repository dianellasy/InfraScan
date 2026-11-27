"""Add nullable object_storage_key to input_media and evidence_media

Revision ID: a1c3e5f70829
Revises: 4b7d2e9c5a18
Create Date: 2026-11-20 12:00:00.000000

Adds a nullable string column holding the R2 object key for each media row.
Additive only: existing BYTEA `data` stays required until the next revision.
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "a1c3e5f70829"
down_revision: str | Sequence[str] | None = "4b7d2e9c5a18"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "input_media",
        sa.Column("object_storage_key", sa.String(), nullable=True),
    )
    op.add_column(
        "evidence_media",
        sa.Column("object_storage_key", sa.String(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("evidence_media", "object_storage_key")
    op.drop_column("input_media", "object_storage_key")
