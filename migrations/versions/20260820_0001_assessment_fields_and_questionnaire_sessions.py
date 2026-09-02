"""add assessment extra fields and questionnaire_sessions table

Revision ID: 20260820_0001
Revises: 20260410_0002
Create Date: 2026-08-20 00:00:01

Adds 5 missing columns to the assessments table (plain_meaning,
functional_impact, care_consideration, disclaimer, needs_safety_followup)
and creates the questionnaire_sessions table. Both are already defined
in the ORM models but were missing from the Alembic migration chain.
"""

from alembic import op
import sqlalchemy as sa


revision = "20260820_0001"
down_revision = "20260410_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Add missing columns to assessments table
    op.add_column("assessments", sa.Column("plain_meaning", sa.Text(), nullable=False, server_default=""))
    op.add_column("assessments", sa.Column("functional_impact", sa.Text(), nullable=False, server_default=""))
    op.add_column("assessments", sa.Column("care_consideration", sa.Text(), nullable=False, server_default=""))
    op.add_column("assessments", sa.Column("disclaimer", sa.Text(), nullable=False, server_default=""))
    op.add_column("assessments", sa.Column("needs_safety_followup", sa.Boolean(), nullable=False, server_default=sa.text("false")))

    # 2. Create questionnaire_sessions table
    op.create_table(
        "questionnaire_sessions",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("assessment_type", sa.String(length=32), nullable=False),
        sa.Column("answers_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="in_progress"),
        sa.Column("current_index", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_questionnaire_sessions_user_id", "questionnaire_sessions", ["user_id"])
    op.create_index("ix_questionnaire_sessions_assessment_type", "questionnaire_sessions", ["assessment_type"])


def downgrade() -> None:
    op.drop_index("ix_questionnaire_sessions_assessment_type", table_name="questionnaire_sessions")
    op.drop_index("ix_questionnaire_sessions_user_id", table_name="questionnaire_sessions")
    op.drop_table("questionnaire_sessions")
    op.drop_column("assessments", "needs_safety_followup")
    op.drop_column("assessments", "disclaimer")
    op.drop_column("assessments", "care_consideration")
    op.drop_column("assessments", "functional_impact")
    op.drop_column("assessments", "plain_meaning")
