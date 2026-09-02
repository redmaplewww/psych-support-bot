"""records & insights M1: assessment source columns + usage_events table

Revision ID: 20260831_0001
Revises: 20260820_0001
Create Date: 2026-08-31 00:00:01

Adds `source` (chat/panel) to assessments and questionnaire_sessions so both
entry points share one history table, and creates usage_events for
commercialization metering (action metadata only, never mood content).
"""

from alembic import op
import sqlalchemy as sa


revision = "20260831_0001"
down_revision = "20260820_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("assessments", sa.Column("source", sa.String(length=16), nullable=False, server_default="chat"))
    op.add_column(
        "questionnaire_sessions",
        sa.Column("source", sa.String(length=16), nullable=False, server_default="chat"),
    )
    op.create_table(
        "usage_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.String(length=64), nullable=False, index=True),
        sa.Column("event_type", sa.String(length=48), nullable=False, index=True),
        sa.Column("metadata_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(), nullable=False, index=True),
    )


def downgrade() -> None:
    op.drop_table("usage_events")
    op.drop_column("questionnaire_sessions", "source")
    op.drop_column("assessments", "source")
