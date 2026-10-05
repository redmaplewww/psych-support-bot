"""Add profile_evolution_jobs and profile_snapshots for async profile workflow.

Revision ID: 20260915_0004
Revises: 20260915_0003
"""

import sqlalchemy as sa
from alembic import op

revision = "20260915_0004"
down_revision = "20260915_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "profile_evolution_jobs",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.String(64), nullable=False, index=True),
        sa.Column("evidence_watermark", sa.Text(), server_default="{}"),
        sa.Column("prompt_version", sa.String(32), server_default=""),
        sa.Column("idempotency_key", sa.String(128), nullable=False, unique=True),
        sa.Column("status", sa.String(16), server_default="pending"),
        sa.Column("attempt_count", sa.Integer(), server_default="0"),
        sa.Column("error_code", sa.String(64), server_default=""),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
    )
    op.create_table(
        "profile_snapshots",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.String(64), nullable=False, index=True),
        sa.Column("version", sa.Integer(), server_default="1"),
        sa.Column("status", sa.String(16), server_default="shadow"),
        sa.Column("content_json", sa.Text(), server_default="{}"),
        sa.Column("support_policy_json", sa.Text(), server_default="{}"),
        sa.Column("evidence_watermark", sa.Text(), server_default="{}"),
        sa.Column("prompt_version", sa.String(32), server_default=""),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("profile_snapshots")
    op.drop_table("profile_evolution_jobs")