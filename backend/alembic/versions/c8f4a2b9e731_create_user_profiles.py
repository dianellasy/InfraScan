"""Create application user profiles for Supabase auth.

Revision ID: c8f4a2b9e731
Revises: 9e2b6d4f1a37
Create Date: 2026-11-17 14:02:35.000000

"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "c8f4a2b9e731"
down_revision: str | Sequence[str] | None = "9e2b6d4f1a37"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "user_profiles",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("email", sa.String(), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("municipality_id", sa.String(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "role IN ('municipal_administrator', 'city_engineer', "
            "'maintenance_crew', 'developer', 'citizen')",
            name=op.f("ck_user_profiles_role_allowed"),
        ),
        sa.PrimaryKeyConstraint("user_id", name=op.f("pk_user_profiles")),
    )
    op.create_index(
        op.f("ix_user_profiles_role"),
        "user_profiles",
        ["role"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_user_profiles_role"),
        table_name="user_profiles",
    )
    op.drop_table("user_profiles")
