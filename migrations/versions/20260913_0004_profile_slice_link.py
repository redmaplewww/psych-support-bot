"""profile-slice link: ProfileBelief.origin_slice_id (P4 provenance)

Revision ID: 20260913_0004
Revises: 20260913_0003
Create Date: 2026-09-13

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "20260913_0004"
down_revision = "20260913_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "profile_beliefs",
        sa.Column("origin_slice_id", sa.String(64), nullable=True),
    )
    op.create_index(
        "ix_profile_beliefs_origin_slice_id",
        "profile_beliefs",
        ["origin_slice_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_profile_beliefs_origin_slice_id", table_name="profile_beliefs")
    op.drop_column("profile_beliefs", "origin_slice_id")
