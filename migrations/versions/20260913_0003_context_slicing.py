"""context slicing: ConversationSlice, SliceSummary, UserTimeProfile

Revision ID: 20260913_0003
Revises: 20260913_0002
Create Date: 2026-09-13

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "20260913_0003"
down_revision = "20260913_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. 创建 conversation_slices 表
    op.create_table(
        "conversation_slices",
        sa.Column("id", sa.String(64), nullable=False),
        sa.Column("session_id", sa.String(64), nullable=False),
        sa.Column("user_id", sa.String(64), nullable=False),
        sa.Column("start_message_id", sa.Integer(), nullable=True),
        sa.Column("end_message_id", sa.Integer(), nullable=True),
        sa.Column("primary_topic", sa.String(64), server_default="", nullable=False),
        sa.Column("topic_vector", sa.Text(), server_default="{}", nullable=False),
        sa.Column("boundary_reason", sa.String(32), server_default="first_message", nullable=False),
        sa.Column("boundary_confidence", sa.Float(), server_default="1.0", nullable=False),
        sa.Column("turn_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("status", sa.String(16), server_default="active", nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_conversation_slices_session_id", "conversation_slices", ["session_id"])
    op.create_index("ix_conversation_slices_user_id", "conversation_slices", ["user_id"])
    op.create_index(
        "ix_conversation_slices_user_status",
        "conversation_slices",
        ["user_id", "status", "updated_at"],
    )

    # 2. 创建 slice_summaries 表
    op.create_table(
        "slice_summaries",
        sa.Column("slice_id", sa.String(64), nullable=False),
        sa.Column("user_id", sa.String(64), nullable=False),
        sa.Column("summary_text", sa.Text(), server_default="", nullable=False),
        sa.Column("key_points", sa.Text(), server_default="[]", nullable=False),
        sa.Column("topics", sa.Text(), server_default="[]", nullable=False),
        sa.Column("relevance_score", sa.Float(), server_default="1.0", nullable=False),
        sa.Column("summary_embedding", sa.Text(), server_default="[]", nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("slice_id"),
    )
    op.create_index("ix_slice_summaries_user_id", "slice_summaries", ["user_id"])
    op.create_index("ix_slice_summaries_created_at", "slice_summaries", ["created_at"])

    # 3. 创建 user_time_profiles 表
    op.create_table(
        "user_time_profiles",
        sa.Column("user_id", sa.String(64), nullable=False),
        sa.Column("avg_gap_minutes", sa.Float(), server_default="0.0", nullable=False),
        sa.Column("median_gap_minutes", sa.Float(), server_default="0.0", nullable=False),
        sa.Column("p25_gap_minutes", sa.Float(), server_default="0.0", nullable=False),
        sa.Column("p75_gap_minutes", sa.Float(), server_default="0.0", nullable=False),
        sa.Column("sleep_start_hour", sa.Integer(), server_default="23", nullable=False),
        sa.Column("sleep_end_hour", sa.Integer(), server_default="7", nullable=False),
        sa.Column("active_windows", sa.Text(), server_default="[]", nullable=False),
        sa.Column("frequency_tier", sa.String(16), server_default="unknown", nullable=False),
        sa.Column("short_gap_threshold_minutes", sa.Float(), server_default="60.0", nullable=False),
        sa.Column("long_gap_threshold_minutes", sa.Float(), server_default="720.0", nullable=False),
        sa.Column("total_sessions", sa.Integer(), server_default="0", nullable=False),
        sa.Column("last_updated", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("user_id"),
    )

    # 4. 给 messages 表添加 slice_id 字段
    op.add_column("messages", sa.Column("slice_id", sa.String(64), nullable=True))
    op.create_index("ix_messages_slice_id", "messages", ["slice_id"])


def downgrade() -> None:
    # 回滚顺序：先删除索引和外键，再删除列，最后删除表
    op.drop_index("ix_messages_slice_id", "messages")
    op.drop_column("messages", "slice_id")

    op.drop_table("user_time_profiles")

    op.drop_index("ix_slice_summaries_created_at", "slice_summaries")
    op.drop_index("ix_slice_summaries_user_id", "slice_summaries")
    op.drop_table("slice_summaries")

    op.drop_index("ix_conversation_slices_user_status", "conversation_slices")
    op.drop_index("ix_conversation_slices_user_id", "conversation_slices")
    op.drop_index("ix_conversation_slices_session_id", "conversation_slices")
    op.drop_table("conversation_slices")
