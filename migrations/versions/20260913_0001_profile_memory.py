"""profile beliefs / belief events / extraction stats

自进化画像三表（K1a）：
- profile_beliefs: belief 当前状态行，(user_id, key) 唯一（防换措辞复活）
- profile_belief_events: append-only 迁移史（审计 + 确认成本归因）
- profile_extraction_stats: P2 全量调用统计（含熔断跳过）

Revision ID: 20260913_0001
Revises: 20260908_0001
Create Date: 2026-09-13 00:00:00
"""

import sqlalchemy as sa
from alembic import op

revision = "20260913_0001"
down_revision = "20260908_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "profile_beliefs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.String(length=64), nullable=False, index=True),
        sa.Column("dimension", sa.String(length=8), nullable=False, index=True),
        sa.Column("key", sa.String(length=128), nullable=False),
        sa.Column("claim_text", sa.Text(), nullable=False),
        sa.Column("value_json", sa.Text(), nullable=False),
        sa.Column("layer", sa.String(length=8), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("evidence_json", sa.Text(), nullable=False),
        sa.Column("origin_stats_id", sa.Integer(), nullable=True),
        sa.Column("origin_session_id", sa.String(length=64), nullable=True),
        sa.Column("last_evidence_session_id", sa.String(length=64), nullable=True),
        sa.Column("first_seen_at", sa.DateTime(), nullable=False),
        sa.Column("last_evidence_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("user_id", "key", name="uq_profile_beliefs_user_key"),
    )
    op.create_table(
        "profile_belief_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("belief_id", sa.Integer(), nullable=False, index=True),
        sa.Column("user_id", sa.String(length=64), nullable=False, index=True),
        sa.Column("event_type", sa.String(length=32), nullable=False),
        sa.Column("evidence_json", sa.Text(), nullable=False),
        sa.Column("detail_json", sa.Text(), nullable=False),
        sa.Column("origin_stats_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "profile_extraction_stats",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.String(length=64), nullable=False, index=True),
        sa.Column("session_id", sa.String(length=64), nullable=True),
        sa.Column("trigger", sa.String(length=32), nullable=False),
        sa.Column("model", sa.String(length=64), nullable=False),
        sa.Column("prompt_tokens", sa.Integer(), nullable=False),
        sa.Column("completion_tokens", sa.Integer(), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("claims_out", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("error", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, index=True),
    )


def downgrade() -> None:
    op.drop_table("profile_extraction_stats")
    op.drop_table("profile_belief_events")
    op.drop_table("profile_beliefs")
