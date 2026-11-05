"""add user profiles

Revision ID: 20260410_0002
Revises: 20260410_0001
Create Date: 2026-04-10 00:10:00
"""

from alembic import op
import sqlalchemy as sa


revision = "20260410_0002"
down_revision = "20260410_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_profiles",
        sa.Column("user_id", sa.String(length=64), primary_key=True),
        sa.Column("display_name", sa.String(length=128), nullable=False),
        sa.Column("primary_concerns", sa.Text(), nullable=False),
        sa.Column("goals", sa.Text(), nullable=False),
        sa.Column("support_preferences", sa.Text(), nullable=False),
        sa.Column("risk_notes", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("user_profiles")
