"""Account-level user control for inferred profile memory.

Revision ID: 20260915_0001
Revises: 20260914_0002
"""

import sqlalchemy as sa
from alembic import op

revision = "20260915_0001"
down_revision = "20260914_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "profile_memory_preferences",
        sa.Column("user_id", sa.String(length=64), primary_key=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("disabled_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("profile_memory_preferences")
