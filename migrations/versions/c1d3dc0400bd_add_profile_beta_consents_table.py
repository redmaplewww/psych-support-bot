"""add profile_beta_consents table

Revision ID: c1d3dc0400bd
Revises: 20260915_0004
Create Date: 2026-09-16 10:28:56.579079
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'c1d3dc0400bd'
down_revision = '20260915_0004'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'profile_beta_consents',
        sa.Column('user_id', sa.String(length=64), nullable=False),
        sa.Column('version', sa.String(length=32), nullable=False),
        sa.Column('accepted_at', sa.DateTime(), nullable=False),
        sa.Column('sensitive_background_enabled', sa.Boolean(), nullable=False, server_default=sa.text('0')),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('user_id'),
    )


def downgrade() -> None:
    op.drop_table('profile_beta_consents')
