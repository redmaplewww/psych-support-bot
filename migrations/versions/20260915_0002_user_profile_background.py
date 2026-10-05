"""Add background_json to user_profiles for structured identity background.

Revision ID: 20260915_0002
Revises: 20260915_0001
"""

import sqlalchemy as sa
from alembic import op

revision = "20260915_0002"
down_revision = "20260915_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("user_profiles", sa.Column("background_json", sa.Text(), server_default="{}"))


def downgrade() -> None:
    op.drop_column("user_profiles", "background_json")
