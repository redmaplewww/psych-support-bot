"""练习报告与确认字段：exercise_records 加三列 + consent 埋点枚举扩展

Revision ID: 20260904_0001
Revises: 20260901_0001
Create Date: 2026-09-04 00:00:01

练习从"tag+可选一句反思"升级为完整报告（步骤回答 + AI 反馈 + 引导对话），
三列均为 Text 默认空——旧记录保持可读，新记录逐步填充。
"""

from alembic import op
import sqlalchemy as sa


revision = "20260904_0001"
down_revision = "20260901_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("exercise_records", sa.Column("step_responses_json", sa.Text(), nullable=False, server_default="[]"))
    op.add_column("exercise_records", sa.Column("ai_feedback", sa.Text(), nullable=False, server_default=""))
    op.add_column(
        "exercise_records", sa.Column("guidance_transcript_json", sa.Text(), nullable=False, server_default="[]")
    )
    op.add_column("exercise_records", sa.Column("risk_flag", sa.String(length=16), nullable=False, server_default=""))


def downgrade() -> None:
    op.drop_column("exercise_records", "risk_flag")
    op.drop_column("exercise_records", "guidance_transcript_json")
    op.drop_column("exercise_records", "ai_feedback")
    op.drop_column("exercise_records", "step_responses_json")
