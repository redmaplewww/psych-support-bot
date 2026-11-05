"""initial schema

Revision ID: 20260410_0001
Revises:
Create Date: 2026-04-10 00:00:01
"""

from alembic import op
import sqlalchemy as sa


revision = "20260410_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "sessions",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("mode", sa.String(length=32), nullable=False),
        sa.Column("risk_level", sa.String(length=32), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_sessions_user_id", "sessions", ["user_id"])
    op.create_table(
        "messages",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("session_id", sa.String(length=64), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("safety_flag", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_messages_session_id", "messages", ["session_id"])
    op.create_table(
        "assessments",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("assessment_type", sa.String(length=32), nullable=False),
        sa.Column("score", sa.Integer(), nullable=False),
        sa.Column("severity_band", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_assessments_user_id", "assessments", ["user_id"])
    op.create_index("ix_assessments_assessment_type", "assessments", ["assessment_type"])
    op.create_table(
        "checkins",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("checkin_date", sa.Date(), nullable=False),
        sa.Column("mood_score", sa.Integer(), nullable=False),
        sa.Column("anxiety_score", sa.Integer(), nullable=False),
        sa.Column("sleep_hours", sa.Float(), nullable=False),
        sa.Column("energy_score", sa.Integer(), nullable=False),
        sa.Column("note", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_checkins_user_id", "checkins", ["user_id"])
    op.create_index("ix_checkins_checkin_date", "checkins", ["checkin_date"])
    op.create_table(
        "risk_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("session_id", sa.String(length=64), nullable=False),
        sa.Column("risk_level", sa.String(length=32), nullable=False),
        sa.Column("risk_reason", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_risk_events_user_id", "risk_events", ["user_id"])
    op.create_index("ix_risk_events_session_id", "risk_events", ["session_id"])
    op.create_index("ix_risk_events_risk_level", "risk_events", ["risk_level"])
    op.create_table(
        "weekly_reports",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_weekly_reports_user_id", "weekly_reports", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_weekly_reports_user_id", table_name="weekly_reports")
    op.drop_table("weekly_reports")
    op.drop_index("ix_risk_events_risk_level", table_name="risk_events")
    op.drop_index("ix_risk_events_session_id", table_name="risk_events")
    op.drop_index("ix_risk_events_user_id", table_name="risk_events")
    op.drop_table("risk_events")
    op.drop_index("ix_checkins_checkin_date", table_name="checkins")
    op.drop_index("ix_checkins_user_id", table_name="checkins")
    op.drop_table("checkins")
    op.drop_index("ix_assessments_assessment_type", table_name="assessments")
    op.drop_index("ix_assessments_user_id", table_name="assessments")
    op.drop_table("assessments")
    op.drop_index("ix_messages_session_id", table_name="messages")
    op.drop_table("messages")
    op.drop_index("ix_sessions_user_id", table_name="sessions")
    op.drop_table("sessions")
    op.drop_table("users")
