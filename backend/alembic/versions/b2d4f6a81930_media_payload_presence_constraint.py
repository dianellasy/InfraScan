"""Make media data nullable and require data or object_storage_key

Revision ID: b2d4f6a81930
Revises: a1c3e5f70829
Create Date: 2026-11-20 12:05:00.000000

Allows R2-only media rows by making `data` nullable and rejects rows with
neither inline bytes nor an object key. Legacy BYTEA rows stay valid.
Downgrade refuses to run while R2-only rows exist, because restoring
`data NOT NULL` would otherwise require inventing payloads.
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
    for table in ("input_media", "evidence_media"):
        op.alter_column(table, "data", existing_type=BYTEA, nullable=True)
        op.create_check_constraint(
            op.f(f"ck_{table}_data_or_object_storage_key"), table, PAYLOAD_CHECK
        )


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM input_media WHERE data IS NULL)
               OR EXISTS (SELECT 1 FROM evidence_media WHERE data IS NULL) THEN
                RAISE EXCEPTION
                    'Cannot downgrade b2d4f6a81930: R2-only media rows have no inline data';
            END IF;
        END
        $$;
        """
    )
    for table in ("evidence_media", "input_media"):
        op.drop_constraint(
            op.f(f"ck_{table}_data_or_object_storage_key"), table, type_="check"
        )
        op.alter_column(table, "data", existing_type=BYTEA, nullable=False)
