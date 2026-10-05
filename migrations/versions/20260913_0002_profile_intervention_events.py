"""profile intervention events (K2c: 干预→反应事件)

Revision ID: 20260913_0002
Revises: 20260913_0001
Create Date: 2026-09-13 02:00:00
"""

from alembic import op
import sqlalchemy as sa


revision = "20260913_0002"
down_revision = "20260913_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "profile_intervention_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.String(length=64), nullable=False, index=True),
        sa.Column("session_id", sa.String(length=64), nullable=False, index=True),
        sa.Column("intervention_kind", sa.String(length=32), nullable=False),
        sa.Column("detail_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, index=True),
    )


def downgrade() -> None:
    op.drop_table("profile_intervention_events")
