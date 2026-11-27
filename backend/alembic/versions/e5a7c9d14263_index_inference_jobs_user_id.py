"""Index inference job owners

Revision ID: e5a7c9d14263
Revises: d4f6a8b03152
Create Date: 2026-11-21 23:50:00.000000

`inference_jobs.user_id` now holds the submitter's Supabase Auth user UUID and
every owner-scoped read filters on it (job lookups, the job → Issue filter,
and evidence and Issue-detection visibility). The column stays nullable for
historical jobs and has no foreign key: Supabase Auth lives in a different
database.

The index is built inside the migration transaction (not CONCURRENTLY) so a
refused or failed upgrade/downgrade rolls back as a unit, like every other
revision. The table is small; the build blocks job inserts only briefly.
"""
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e5a7c9d14263"
down_revision: str | Sequence[str] | None = "d4f6a8b03152"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index("ix_inference_jobs_user_id", "inference_jobs", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_inference_jobs_user_id", table_name="inference_jobs")
