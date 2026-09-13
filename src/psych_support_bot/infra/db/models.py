from datetime import UTC, date, datetime

from sqlalchemy import Boolean, Date, DateTime, Float, Integer, String, Text, UniqueConstraint
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


class PracticeSessionRecord(Base):
    """图内引导练习会话（对话式 54321 等）。

    与问卷会话同构：GraphState 每轮从 DB 重建，多轮练习的推进状态必须
    落库；current_step 是"正在等待用户回答"的步骤下标（0-based）。
    status: active（进行中）/ paused（暂停，保留已答步骤）/ completed。
    """

    __tablename__ = "practice_sessions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    exercise_tag: Mapped[str] = mapped_column(String(64), index=True)
    current_step: Mapped[int] = mapped_column(Integer, default=0)
    step_responses_json: Mapped[str] = mapped_column(Text, default="[]")
    guidance_transcript_json: Mapped[str] = mapped_column(Text, default="[]")
    status: Mapped[str] = mapped_column(String(16), default="active")
    disclaimer_version: Mapped[str] = mapped_column(String(32), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


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


class ProfileBelief(Base):
    """画像信念——自进化画像的"当前状态"行。

    画像不是静态标签表而是 belief 流：本表存当前状态，完整迁移史在
    profile_belief_events（append-only，可审计、可回答"你为什么认为我
    XX"）；用户删除画像 = 两表按 user_id 级联删（接入 me_repositories）。

    结构性红线（约束写在代码里，不靠提取器自觉）：
    - (user_id, key) 唯一：对 key 而非文本去重——被否决的 belief 换措辞
      重现仍命中同一 key（防"换措辞复活"）。
    - source="extracted" 的行 layer 强制 L4（仓储层钳制）：L2 只能来自
      用户确认或程序硬证据，提取器产出永远是待验证假设。
    - key 以 "distortion." 开头的写入在仓储层直接拒绝：认知歪曲只许
      当轮识别（identify_only），结构上不给沉淀入口。
    - 风险内容不入本表：无任何"风险类"字段——自伤/自杀信号只走
      RiskEvent 结构化通道，画像层零存储。

    字段语义：
    - layer: L1 用户自述 / L2 已确认演化状态 / L3 情境信号 / L4 待验证假设。
    - status: active / rejected；rejected 即 D8 负记忆，同 key 永不复活。
    - claim_text: 临床中性观察句（内部语言；用户可见措辞由展示词典层
      渲染，术语不出存储层，见 PROFILE_DECISIONS P3）。
    - confidence: 0-1，支持证据推进、矛盾证据衰减；≥0.7 且跨会话证据
      ≥2 才有资格进 L4 质询候选池（升级 L2 仍需用户确认）。
    - last_evidence_at: 90 天无新证据降权排序的时钟基准（不删除）。
    """

    __tablename__ = "profile_beliefs"
    __table_args__ = (UniqueConstraint("user_id", "key", name="uq_profile_beliefs_user_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    dimension: Mapped[str] = mapped_column(String(8), index=True)
    key: Mapped[str] = mapped_column(String(128))
    claim_text: Mapped[str] = mapped_column(Text, default="")
    value_json: Mapped[str] = mapped_column(Text, default="{}")
    layer: Mapped[str] = mapped_column(String(8), default="L4")
    status: Mapped[str] = mapped_column(String(16), default="active")
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    source: Mapped[str] = mapped_column(String(32), default="extracted")
    evidence_json: Mapped[str] = mapped_column(Text, default="[]")
    origin_stats_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    origin_session_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_evidence_session_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_evidence_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class ProfileBeliefEvent(Base):
    """信念事件日志（append-only）。

    event_type: created / supported / contradicted / downgraded /
    confirmed / rejected / user_edited / value_updated。只插入不更新——审计与
    "每条被确认信念的成本"归因（origin_stats_id → 提取调用统计）都靠它。
    """

    __tablename__ = "profile_belief_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    belief_id: Mapped[int] = mapped_column(Integer, index=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    event_type: Mapped[str] = mapped_column(String(32))
    evidence_json: Mapped[str] = mapped_column(Text, default="[]")
    detail_json: Mapped[str] = mapped_column(Text, default="{}")
    origin_stats_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class ProfileExtractionStats(Base):
    """画像提取调用全量统计（PROFILE_DECISIONS P2：先不设上限，全量统计）。

    每次提取调用一行；熔断阀跳过也记（status="skipped"）——跳过率本身
    是预算分析素材。决策指标是"每条被确认信念的成本"（belief 的
    origin_stats_id 可追溯到调用行），不是"每次调用的成本"。只记元数据
    不记 prompt/响应内容（与 UsageEvent 伦理边界同源）。
    """

    __tablename__ = "profile_extraction_stats"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    session_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    trigger: Mapped[str] = mapped_column(String(32), default="")
    model: Mapped[str] = mapped_column(String(64), default="")
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    claims_out: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(24), default="ok")
    error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


class ProfileInterventionEvent(Base):
    """干预→反应事件（K2c：干预即实验的记录半边）。

    只记动作元数据（练习 tag / 注入的画像假设标签），绝不记对话内容
    （UsageEvent 伦理边界同源）。结果变量由事件序列派生而非另存：
    - practice_offer 之后同 tag 的 start/complete = 接受；
    - question_injected 之后对应 belief 的 confirmed/rejected 事件 = 质询结局。
    intervention_kind: practice_offer / practice_start / practice_complete /
    question_injected。
    """

    __tablename__ = "profile_intervention_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    session_id: Mapped[str] = mapped_column(String(64), index=True)
    intervention_kind: Mapped[str] = mapped_column(String(32))
    detail_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
