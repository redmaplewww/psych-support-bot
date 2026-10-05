"""Add understanding_json to user_profiles for K3 synthesis.

Revision ID: 20260915_0003
Revises: 20260915_0002
"""

import sqlalchemy as sa
from alembic import op

revision = "20260915_0003"
down_revision = "20260915_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("user_profiles", sa.Column("understanding_json", sa.Text(), server_default="{}"))


def downgrade() -> None:
    op.drop_column("user_profiles", "understanding_json")
