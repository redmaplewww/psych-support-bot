from __future__ import annotations

import json
import logging
from datetime import date, timedelta
from uuid import uuid4

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from psych_support_bot.ai.schemas.messages import ConversationResponse
from psych_support_bot.domain.assessments.schemas import (
    AssessmentResult,
    AssessmentScore,
    AssessmentType,
)
from psych_support_bot.domain.checkins.schemas import DailyCheckin
from psych_support_bot.infra.db.base import Base
from psych_support_bot.infra.db.models import (
    AssessmentRecord,
    CheckinRecord,
    ConversationSession,
    Message,
    QuestionnaireSessionRecord,
    RiskEvent,
    UsageEvent,
    User,
    UserProfile,
    WeeklyReportRecord,
    utcnow,
)

logger = logging.getLogger(__name__)


def _safe(text: str) -> str:
    return text.replace(" || ", " ").replace(" | ", " ")


def ensure_user(session: Session, user_id: str) -> User:
    user = session.get(User, user_id)
    if user is None:
        user = User(id=user_id)
        session.add(user)
        session.flush()
    return user


def upsert_user_profile(
    session: Session,
    user_id: str,
    display_name: str,
    primary_concerns: str,
    goals: str,
    support_preferences: str,
    risk_notes: str,
) -> UserProfile:
    ensure_user(session, user_id)
    profile = session.get(UserProfile, user_id)
    if profile is None:
        profile = UserProfile(user_id=user_id)
        session.add(profile)

    profile.display_name = display_name
    profile.primary_concerns = primary_concerns
    profile.goals = goals
    profile.support_preferences = support_preferences
    profile.risk_notes = risk_notes
    session.commit()
    session.refresh(profile)
    return profile


def get_user_profile(session: Session, user_id: str) -> UserProfile | None:
    return session.get(UserProfile, user_id)


def get_latest_summary(session: Session, user_id: str) -> str:
    stmt = (
        select(ConversationSession.summary)
        .where(ConversationSession.user_id == user_id)
        .order_by(desc(ConversationSession.created_at))
        .limit(1)
    )
    return session.execute(stmt).scalar_one_or_none() or ""


def get_recent_messages(session: Session, user_id: str, limit: int = 10) -> list[str]:
    """Most recent messages across the user's last sessions, role-labelled.

    Kept as speaker-annotated strings (``User:`` / ``Bot:``) so the memory
    snapshot can ground the model in what actually happened, including which
    side said it.
    """
    session_ids_stmt = (
        select(ConversationSession.id)
        .where(ConversationSession.user_id == user_id)
        .order_by(desc(ConversationSession.created_at))
        .limit(3)
    )
    session_ids = list(session.execute(session_ids_stmt).scalars())
    if not session_ids:
        return []

    stmt = (
        select(Message.role, Message.content)
        .where(Message.session_id.in_(session_ids))
        .order_by(desc(Message.created_at))
        .limit(limit)
    )
    rows = list(session.execute(stmt))
    formatted = []
    for role, content in rows:
        prefix = "User" if role == "user" else "Bot"
        formatted.append(f"{prefix}: {content}")
    return formatted


def get_user_sessions(session: Session, user_id: str, limit: int = 20) -> list[ConversationSession]:
    stmt = (
        select(ConversationSession)
        .where(ConversationSession.user_id == user_id)
        .order_by(desc(ConversationSession.created_at))
        .limit(limit)
    )
    return list(session.execute(stmt).scalars())


def get_session_messages(session: Session, session_id: str) -> list[Message]:
    stmt = select(Message).where(Message.session_id == session_id).order_by(Message.created_at)
    return list(session.execute(stmt).scalars())


def get_user_risk_events(session: Session, user_id: str, limit: int = 20) -> list[RiskEvent]:
    stmt = select(RiskEvent).where(RiskEvent.user_id == user_id).order_by(desc(RiskEvent.created_at)).limit(limit)
    return list(session.execute(stmt).scalars())


def get_recent_assessment_summary(session: Session, user_id: str) -> str:
    records = get_user_assessments(session, user_id, limit=3)
    if not records:
        return ""
    return "; ".join(f"{record.assessment_type}:{record.score}({record.severity_band})" for record in records)


def get_user_assessments(session: Session, user_id: str, *, limit: int = 50) -> list[AssessmentRecord]:
    stmt = (
        select(AssessmentRecord)
        .where(AssessmentRecord.user_id == user_id)
        .order_by(desc(AssessmentRecord.created_at))
        .limit(limit)
    )
    return list(session.execute(stmt).scalars())


def build_memory_snapshot(session: Session, user_id: str) -> str:
    latest_summary = get_latest_summary(session, user_id)
    recent_messages = get_recent_messages(session, user_id)
    assessment_summary = get_recent_assessment_summary(session, user_id)
    recent_checkins = get_recent_checkins(session, user_id, limit=3)
    profile = get_user_profile(session, user_id)

    checkin_summary = ""
    if recent_checkins:
        avg_mood = sum(item.mood_score for item in recent_checkins) / len(recent_checkins)
        avg_anxiety = sum(item.anxiety_score for item in recent_checkins) / len(recent_checkins)
        checkin_summary = f"recent check-ins mood={avg_mood:.1f}/10 anxiety={avg_anxiety:.1f}/10"

    # Last five turns with speaker labels — thin excerpts were the root cause
    # of the bot forgetting events like "we just finished a breathing exercise"
    # and re-asking the user whether they wanted to start one.
    recent_excerpt = "\n".join(_safe(msg) for msg in reversed(recent_messages[-5:])) if recent_messages else ""
    profile_summary = ""
    if profile is not None:
        profile_summary = " || ".join(
            _safe(piece)
            for piece in [
                profile.primary_concerns,
                profile.goals,
                profile.support_preferences,
                profile.risk_notes,
            ]
            if piece
        )
    pieces = [
        piece
        for piece in [
            profile_summary,
            latest_summary,
            assessment_summary,
            checkin_summary,
            recent_excerpt,
        ]
        if piece
    ]
    return " || ".join(pieces)


def save_conversation_result(
    session: Session,
    response: ConversationResponse,
    user_message: str,
    user_id: str,
) -> None:
    ensure_user(session, user_id)
    session.merge(
        ConversationSession(
            id=response.session_id,
            user_id=user_id,
            mode=response.mode,
            risk_level=response.risk.risk_level,
            summary=response.summary,
        )
    )
    session.add(
        Message(
            session_id=response.session_id,
            role="user",
            content=user_message,
            safety_flag=response.risk.needs_crisis_mode,
        )
    )
    session.add(
        Message(
            session_id=response.session_id,
            role="assistant",
            content=response.reply.text,
            safety_flag=response.risk.needs_crisis_mode,
        )
    )
    if response.risk.risk_level in {"high", "critical"}:
        session.add(
            RiskEvent(
                user_id=user_id,
                session_id=response.session_id,
                risk_level=response.risk.risk_level,
                risk_reason=response.risk.reason,
            )
        )
    session.commit()


def save_assessment(
    session: Session, user_id: str, assessment: AssessmentScore, *, source: str = "chat"
) -> AssessmentRecord:
    ensure_user(session, user_id)
    record = AssessmentRecord(
        user_id=user_id,
        assessment_type=assessment.assessment_type,
        score=assessment.score,
        severity_band=assessment.severity_band,
        source=source,
    )
    if isinstance(assessment, AssessmentResult) and assessment.interpretation:
        interp = assessment.interpretation
        record.plain_meaning = interp.plain_meaning
        record.functional_impact = interp.functional_impact
        record.care_consideration = interp.care_consideration
        record.disclaimer = interp.disclaimer
        record.needs_safety_followup = interp.needs_safety_followup
    session.add(record)
    record_usage_event(session, user_id, "assessment_submitted")
    session.commit()
    session.refresh(record)
    return record


_ALLOWED_USAGE_EVENTS = {
    "exercise_completed",
    "assessment_submitted",
    "checkin_created",
    "checkin_backfilled",
    "ai_analysis_requested",
    "ai_analysis_served",
}


def record_usage_event(session: Session, user_id: str, event_type: str, **metadata: object) -> None:
    """商业化计量埋点：只记动作元数据，绝不写情绪内容（伦理边界见 UsageEvent 注释）。

    埋点失败不阻断主流程——用 savepoint 隔离，失败只丢弃这一条事件，
    不回滚外层事务里正在进行的正常写入。
    """
    if event_type not in _ALLOWED_USAGE_EVENTS:
        raise ValueError(f"Unknown usage event type: {event_type!r}")
    try:
        with session.begin_nested():
            session.add(
                UsageEvent(
                    user_id=user_id,
                    event_type=event_type,
                    metadata_json=json.dumps(metadata or {}, ensure_ascii=False, default=str),
                )
            )
    except Exception:
        logger.warning("Usage event recording failed (non-blocking): %s", event_type, exc_info=True)


def create_questionnaire_session(
    session: Session, user_id: str, assessment_type: AssessmentType
) -> QuestionnaireSessionRecord:
    existing = get_active_questionnaire_session(session, user_id)
    if existing is not None and existing.assessment_type == assessment_type:
        return existing
    bind = session.get_bind()
    Base.metadata.create_all(bind=bind)
    ensure_user(session, user_id)
    record = QuestionnaireSessionRecord(
        id=str(uuid4()),
        user_id=user_id,
        assessment_type=assessment_type,
        answers_json="[]",
        status="in_progress",
        current_index=0,
    )
    session.add(record)
    session.commit()
    session.refresh(record)
    return record


def get_questionnaire_session(session: Session, session_id: str) -> QuestionnaireSessionRecord | None:
    return session.get(QuestionnaireSessionRecord, session_id)


def get_active_questionnaire_session(session: Session, user_id: str) -> QuestionnaireSessionRecord | None:
    stmt = (
        select(QuestionnaireSessionRecord)
        .where(QuestionnaireSessionRecord.user_id == user_id)
        .where(QuestionnaireSessionRecord.status == "in_progress")
        .order_by(desc(QuestionnaireSessionRecord.created_at))
        .limit(1)
    )
    return session.execute(stmt).scalar_one_or_none()


def append_questionnaire_answer(
    session: Session, session_record: QuestionnaireSessionRecord, value: int
) -> QuestionnaireSessionRecord:
    answers = json.loads(session_record.answers_json or "[]")
    answers.append(value)
    session_record.answers_json = json.dumps(answers)
    session_record.current_index = len(answers)
    session.commit()
    session.refresh(session_record)
    return session_record


def complete_questionnaire_session(
    session: Session, session_record: QuestionnaireSessionRecord
) -> QuestionnaireSessionRecord:
    session_record.status = "completed"
    session_record.completed_at = utcnow()
    session.commit()
    session.refresh(session_record)
    return session_record


def pause_questionnaire_session(
    session: Session, session_record: QuestionnaireSessionRecord
) -> QuestionnaireSessionRecord:
    """Put an in-progress questionnaire on hold, keeping partial answers."""
    session_record.status = "paused"
    session.commit()
    session.refresh(session_record)
    return session_record


def resume_questionnaire_session(
    session: Session, session_record: QuestionnaireSessionRecord
) -> QuestionnaireSessionRecord:
    session_record.status = "in_progress"
    session.commit()
    session.refresh(session_record)
    return session_record


def get_paused_questionnaire_session(
    session: Session, user_id: str, assessment_type: str
) -> QuestionnaireSessionRecord | None:
    stmt = (
        select(QuestionnaireSessionRecord)
        .where(QuestionnaireSessionRecord.user_id == user_id)
        .where(QuestionnaireSessionRecord.assessment_type == assessment_type)
        .where(QuestionnaireSessionRecord.status == "paused")
        .order_by(desc(QuestionnaireSessionRecord.updated_at))
        .limit(1)
    )
    return session.execute(stmt).scalar_one_or_none()


def get_latest_assessment(session: Session, user_id: str, assessment_type: str) -> AssessmentRecord | None:
    stmt = (
        select(AssessmentRecord)
        .where(AssessmentRecord.user_id == user_id)
        .where(AssessmentRecord.assessment_type == assessment_type)
        .order_by(desc(AssessmentRecord.created_at))
        .limit(1)
    )
    return session.execute(stmt).scalar_one_or_none()


def get_checkin_on_date(session: Session, user_id: str, checkin_date: date) -> CheckinRecord | None:
    return (
        session.query(CheckinRecord)  # type: ignore[attr-defined]
        .filter(CheckinRecord.user_id == user_id)  # 绑定参数，非字符串拼接
        .filter(CheckinRecord.checkin_date == checkin_date)
        .order_by(CheckinRecord.created_at.desc())
        .first()
    )


def save_checkin(session: Session, user_id: str, checkin: DailyCheckin) -> CheckinRecord:
    """按 (user_id, checkin_date) 幂等 upsert。

    未带 checkin_date 时视为"今天打卡"（同日已存在则覆盖）；
    携带日期时用于本地历史补传——覆盖四维与备注，不虚增埋点。
    """
    ensure_user(session, user_id)
    target_date = checkin.checkin_date or date.today()  # noqa: DTZ011
    record = get_checkin_on_date(session, user_id, target_date)
    is_new = record is None
    if is_new:
        record = CheckinRecord(
            user_id=user_id,
            checkin_date=target_date,
            mood_score=checkin.mood_score,
            anxiety_score=checkin.anxiety_score,
            sleep_hours=checkin.sleep_hours,
            energy_score=checkin.energy_score,
            note=checkin.note or "",
        )
        session.add(record)
    else:
        record.mood_score = checkin.mood_score
        record.anxiety_score = checkin.anxiety_score
        record.sleep_hours = checkin.sleep_hours
        record.energy_score = checkin.energy_score
        record.note = checkin.note or ""
    # 埋点放在构造完整之后：record_usage_event 内部 flush，半成品对象会触发 NOT NULL
    if is_new:
        event_type = "checkin_backfilled" if checkin.checkin_date else "checkin_created"
        record_usage_event(session, user_id, event_type)
    session.commit()
    session.refresh(record)
    return record


def get_recent_checkins(session: Session, user_id: str, limit: int = 7) -> list[CheckinRecord]:
    stmt = (
        select(CheckinRecord)
        .where(CheckinRecord.user_id == user_id)
        .order_by(desc(CheckinRecord.checkin_date), desc(CheckinRecord.created_at))
        .limit(limit)
    )
    return list(session.execute(stmt).scalars())


def get_checkins_since(session: Session, user_id: str, *, days: int = 30) -> list[CheckinRecord]:
    """近 N 天打卡记录，按日期倒序（记录查看用，笔记只返回给用户本人）。"""
    cutoff = date.today() - timedelta(days=days)  # noqa: DTZ011
    stmt = (
        select(CheckinRecord)
        .where(CheckinRecord.user_id == user_id)
        .where(CheckinRecord.checkin_date >= cutoff)
        .order_by(desc(CheckinRecord.checkin_date), desc(CheckinRecord.created_at))
        .limit(days)
    )
    return list(session.execute(stmt).scalars())


def save_weekly_report(session: Session, user_id: str, summary: str) -> WeeklyReportRecord:
    record = WeeklyReportRecord(user_id=user_id, summary=summary)
    session.add(record)
    session.commit()
    session.refresh(record)
    return record
