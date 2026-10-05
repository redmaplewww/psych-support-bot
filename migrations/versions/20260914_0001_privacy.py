"""Durable privacy consent and external deletion receipts."""

import sqlalchemy as sa
from alembic import op

revision = "20260914_0001"
down_revision = "20260913_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "privacy_consents",
        sa.Column("user_id", sa.String(64), primary_key=True),
        sa.Column("version", sa.String(32), nullable=False),
        sa.Column("accepted_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "privacy_deletion_jobs",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.String(64), nullable=True),
        sa.Column("session_ids_json", sa.Text(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("error_code", sa.String(48), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_privacy_deletion_jobs_user_id", "privacy_deletion_jobs", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_privacy_deletion_jobs_user_id", table_name="privacy_deletion_jobs")
    op.drop_table("privacy_deletion_jobs")
    op.drop_table("privacy_consents")
