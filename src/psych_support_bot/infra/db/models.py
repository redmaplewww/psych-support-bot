from datetime import UTC, date, datetime

from sqlalchemy import Boolean, Date, DateTime, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from psych_support_bot.infra.db.base import Base


def utcnow() -> datetime:
    return datetime.now(UTC)


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class UserCredential(Base):
    """登录凭据（JWT 认证）。

    密码只存 pbkdf2_sha256 哈希（api/auth.py），绝不明文落库。
    user_id 同时是 users.id / 全库数据关联键：注册即建同值 User，
    既有客户端自报 user_id 的历史数据不受影响。
    """

    __tablename__ = "user_credentials"

    user_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(256))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class UserProfile(Base):
    __tablename__ = "user_profiles"

    user_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(128), default="")
    primary_concerns: Mapped[str] = mapped_column(Text, default="")
    goals: Mapped[str] = mapped_column(Text, default="")
    support_preferences: Mapped[str] = mapped_column(Text, default="")
    risk_notes: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class ConversationSession(Base):
    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    mode: Mapped[str] = mapped_column(String(32))
    risk_level: Mapped[str] = mapped_column(String(32))
    summary: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String(64), index=True)
    role: Mapped[str] = mapped_column(String(32))
    content: Mapped[str] = mapped_column(Text)
    safety_flag: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class AssessmentRecord(Base):
    __tablename__ = "assessments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    assessment_type: Mapped[str] = mapped_column(String(32), index=True)
    score: Mapped[int] = mapped_column(Integer)
    severity_band: Mapped[str] = mapped_column(String(32))
    plain_meaning: Mapped[str] = mapped_column(Text, default="")
    functional_impact: Mapped[str] = mapped_column(Text, default="")
    care_consideration: Mapped[str] = mapped_column(Text, default="")
    disclaimer: Mapped[str] = mapped_column(Text, default="")
    needs_safety_followup: Mapped[bool] = mapped_column(Boolean, default=False)
    # 来源渠道（chat=对话图内完成 / panel=页面直接提交）——仅用于来源分析，
    # 历史记录不分渠道存储，两个入口共享同一条趋势线。
    source: Mapped[str] = mapped_column(String(16), default="chat", server_default="chat")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class QuestionnaireSessionRecord(Base):
    __tablename__ = "questionnaire_sessions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    assessment_type: Mapped[str] = mapped_column(String(32), index=True)
    answers_json: Mapped[str] = mapped_column(Text, default="[]")
    status: Mapped[str] = mapped_column(String(32), default="in_progress")
    current_index: Mapped[int] = mapped_column(Integer, default=0)
    # 来源渠道，语义同 assessments.source
    source: Mapped[str] = mapped_column(String(16), default="chat", server_default="chat")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class CheckinRecord(Base):
    __tablename__ = "checkins"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    checkin_date: Mapped[date] = mapped_column(Date, index=True)
    mood_score: Mapped[int] = mapped_column(Integer)
    anxiety_score: Mapped[int] = mapped_column(Integer)
    sleep_hours: Mapped[float] = mapped_column(Float)
    energy_score: Mapped[int] = mapped_column(Integer)
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class RiskEvent(Base):
    __tablename__ = "risk_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    session_id: Mapped[str] = mapped_column(String(64), index=True)
    risk_level: Mapped[str] = mapped_column(String(32), index=True)
    risk_reason: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class WeeklyReportRecord(Base):
    __tablename__ = "weekly_reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    summary: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class PlanEnrollment(Base):
    __tablename__ = "plan_enrollments"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    plan_id: Mapped[str] = mapped_column(String(64))
    enrolled_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    completed_days_json: Mapped[str] = mapped_column(Text, default="[]")
    current_day: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(32), default="active")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class UsageEvent(Base):
    """商业化计量埋点（动作元数据）。

    伦理边界：只记动作类型/时间/次数，绝不记录情绪内容
    （mood 分数、note、练习反思）——那些只属于用户自己的趋势功能。
    事件类型固定枚举：exercise_completed / assessment_submitted /
    checkin_created / ai_analysis_requested / ai_analysis_served /
    auth_register / auth_login / auth_login_failed。
    """

    __tablename__ = "usage_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    event_type: Mapped[str] = mapped_column(String(48), index=True)
    metadata_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


class ExerciseRecord(Base):
    """练习完成记录（M3 之前练习完全不落库）。

    source 语义同 assessments.source：chat=对话图内完成 / panel=页面练习面板完成。
    reflection_note 是用户自己的反思内容——只用于本人记录展示与本人 AI 分析输入，
    不进商业化埋点（UsageEvent 伦理边界）。

    报告字段（20260904_0001，用户须知同意后的主动交互场景）：
    - step_responses_json: 面板分步回答（JSON 数组，索引对应步骤）
    - ai_feedback: 完成后的 AI 个人化反馈（读取步骤回答生成——被动统计
      不碰内容的旧边界不变；这是经须知确认的主动交互例外）
    - guidance_transcript_json: 完成后 AI 对话引导的轮次记录（JSON 数组）
    """

    __tablename__ = "exercise_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    exercise_tag: Mapped[str] = mapped_column(String(64))
    source: Mapped[str] = mapped_column(String(16), default="chat", server_default="chat")
    reflection_note: Mapped[str] = mapped_column(Text, default="")
    step_responses_json: Mapped[str] = mapped_column(Text, default="[]", server_default="[]")
    ai_feedback: Mapped[str] = mapped_column(Text, default="", server_default="")
    guidance_transcript_json: Mapped[str] = mapped_column(Text, default="[]", server_default="[]")
    # 风险标记（""/"elevated"/"high"/"critical"）：完成时筛查命中则记录，
    # 记录列表展示"需要关照"徽标（只记等级词，不记内容）。
    risk_flag: Mapped[str] = mapped_column(String(16), default="", server_default="")
    completed_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
