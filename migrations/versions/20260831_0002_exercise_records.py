"""records & insights M3: exercise_records table

Revision ID: 20260831_0002
Revises: 20260831_0001
Create Date: 2026-08-31 00:00:02

练习此前完全不落库；本迁移建 exercise_records（user/exercise_tag/source/
reflection_note/completed_at），对话内完成与页面完成共用一张表。
"""

from alembic import op
import sqlalchemy as sa


revision = "20260831_0002"
down_revision = "20260831_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "exercise_records",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.String(length=64), nullable=False, index=True),
        sa.Column("exercise_tag", sa.String(length=64), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False, server_default="chat"),
        sa.Column("reflection_note", sa.Text(), nullable=False, server_default=""),
        sa.Column("completed_at", sa.DateTime(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("exercise_records")
