"""Record the human review decision on an Issue

Revision ID: f6b8d0e2a374
Revises: e5a7c9d14263
Create Date: 2026-11-26 12:00:00.000000

A reviewer (developer or municipal administrator) accepts, resolves or
dismisses an Issue from the Detection Review page. `reviewed_by` is the
reviewer's Supabase user UUID (no foreign key, like inference_jobs.user_id),
`reviewed_at` when they decided, and `review_note` their optional note.
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "f6b8d0e2a374"
down_revision: str | Sequence[str] | None = "e5a7c9d14263"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("issues", sa.Column("review_note", sa.Text(), nullable=True))
    op.add_column("issues", sa.Column("reviewed_by", sa.Uuid(), nullable=True))
    op.add_column(
        "issues", sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("issues", "reviewed_at")
    op.drop_column("issues", "reviewed_by")
    op.drop_column("issues", "review_note")
