"""Create issue_types and issues; link detection_records.issue_id

Revision ID: 4b7d2e9c5a18
Revises: 9e2b6d4f1a37
Create Date: 2026-11-20 03:30:00.000000

`issue_types` is reference data inserted here, including the mapping from the
v0-mvp model class IDs (0 D00, 1 D10, 2 D20, 3 D40). `detection_records.issue_type`
keeps the raw predicted class ID and intentionally gets no foreign key, so
classification conflicts within an Issue remain visible. Existing detections
keep `issue_id` NULL until they are assigned to an Issue.
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "4b7d2e9c5a18"
down_revision: str | Sequence[str] | None = "9e2b6d4f1a37"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ISSUE_TYPES = (
    ("longitudinal_crack", "Longitudinal crack", 0),
    ("transverse_crack", "Transverse crack", 1),
    ("alligator_crack", "Alligator crack", 2),
    ("pothole", "Pothole", 3),
)


def upgrade() -> None:
    """Create canonical issue persistence and link detections to Issues."""
    issue_types = op.create_table(
        "issue_types",
        sa.Column("code", sa.String(length=32), nullable=False),
        sa.Column("display_name", sa.String(length=64), nullable=False),
        sa.Column("model_class_id", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("code", name=op.f("pk_issue_types")),
        sa.UniqueConstraint(
            "model_class_id", name=op.f("uq_issue_types_model_class_id")
        ),
    )
    op.bulk_insert(
        issue_types,
        [
            {"code": code, "display_name": name, "model_class_id": class_id}
            for code, name, class_id in ISSUE_TYPES
        ],
    )

    op.create_table(
        "issues",
        sa.Column(
            "issue_id",
            sa.Uuid(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("issue_type_code", sa.String(length=32), nullable=False),
        sa.Column("severity", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("review_reason", sa.String(length=32), nullable=True),
        sa.Column("latitude", sa.Double(), nullable=True),
        sa.Column("longitude", sa.Double(), nullable=True),
        sa.Column("location_accuracy_m", sa.Double(), nullable=True),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
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
            "severity IN ('minor', 'moderate', 'critical')",
            name=op.f("ck_issues_severity_allowed"),
        ),
        sa.CheckConstraint(
            "status IN ('pending_review', 'accepted', 'resolved', 'dismissed')",
            name=op.f("ck_issues_status_allowed"),
        ),
        sa.CheckConstraint(
            "review_reason IS NULL OR review_reason IN "
            "('type_conflict', 'missing_location', 'low_confidence')",
            name=op.f("ck_issues_review_reason_allowed"),
        ),
        sa.CheckConstraint(
            "(latitude IS NULL) = (longitude IS NULL)",
            name=op.f("ck_issues_coordinates_paired"),
        ),
        sa.CheckConstraint(
            "latitude >= -90 AND latitude <= 90",
            name=op.f("ck_issues_latitude_range"),
        ),
        sa.CheckConstraint(
            "longitude >= -180 AND longitude <= 180",
            name=op.f("ck_issues_longitude_range"),
        ),
        sa.CheckConstraint(
            "location_accuracy_m >= 0",
            name=op.f("ck_issues_location_accuracy_nonnegative"),
        ),
        sa.CheckConstraint(
            "last_seen_at >= first_seen_at",
            name=op.f("ck_issues_seen_order"),
        ),
        sa.ForeignKeyConstraint(
            ["issue_type_code"],
            ["issue_types.code"],
            name=op.f("fk_issues_issue_type_code_issue_types"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("issue_id", name=op.f("pk_issues")),
    )
    op.create_index(
        op.f("ix_issues_issue_type_code"), "issues", ["issue_type_code"], unique=False
    )
    op.create_index(op.f("ix_issues_status"), "issues", ["status"], unique=False)
    op.create_index(
        op.f("ix_issues_last_seen_at"), "issues", ["last_seen_at"], unique=False
    )
    op.create_index(
        "ix_issues_location",
        "issues",
        [sa.text("point(longitude, latitude)")],
        unique=False,
        postgresql_using="gist",
        postgresql_where=sa.text("latitude IS NOT NULL AND longitude IS NOT NULL"),
    )

    op.create_foreign_key(
        op.f("fk_detection_records_issue_id_issues"),
        "detection_records",
        "issues",
        ["issue_id"],
        ["issue_id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        op.f("ix_detection_records_issue_id"),
        "detection_records",
        ["issue_id"],
        unique=False,
    )


def downgrade() -> None:
    """Remove only the objects created by this revision in reverse FK order."""
    op.drop_index(
        op.f("ix_detection_records_issue_id"), table_name="detection_records"
    )
    op.drop_constraint(
        op.f("fk_detection_records_issue_id_issues"),
        "detection_records",
        type_="foreignkey",
    )
    op.drop_index("ix_issues_location", table_name="issues")
    op.drop_index(op.f("ix_issues_last_seen_at"), table_name="issues")
    op.drop_index(op.f("ix_issues_status"), table_name="issues")
    op.drop_index(op.f("ix_issues_issue_type_code"), table_name="issues")
    op.drop_table("issues")
    op.drop_table("issue_types")
