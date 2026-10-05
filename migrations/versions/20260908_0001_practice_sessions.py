"""add practice sessions (in-chat guided exercise state)

Revision ID: 20260908_0001
Revises: 20260904_0001
Create Date: 2026-09-08 00:10:00
"""

from alembic import op
import sqlalchemy as sa


revision = "20260908_0001"
down_revision = "20260904_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "practice_sessions",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("user_id", sa.String(length=64), nullable=False, index=True),
        sa.Column("exercise_tag", sa.String(length=64), nullable=False, index=True),
        sa.Column("current_step", sa.Integer(), nullable=False),
        sa.Column("step_responses_json", sa.Text(), nullable=False),
        sa.Column("guidance_transcript_json", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("disclaimer_version", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("practice_sessions")
