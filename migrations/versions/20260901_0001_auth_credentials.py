"""JWT 认证：user_credentials 表

Revision ID: 20260901_0001
Revises: 20260831_0002
Create Date: 2026-09-01 00:00:01

用户认证此前完全缺位（user_id 客户端自报）。本迁移建 user_credentials
（username 唯一索引 + pbkdf2 哈希），为 JWT 签发提供存储。
"""

from alembic import op
import sqlalchemy as sa


revision = "20260901_0001"
down_revision = "20260831_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_credentials",
        sa.Column("user_id", sa.String(length=64), primary_key=True),
        sa.Column("username", sa.String(length=64), nullable=False, unique=True, index=True),
        sa.Column("password_hash", sa.String(length=256), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("user_credentials")
