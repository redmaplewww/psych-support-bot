from __future__ import annotations

import json
from datetime import date
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
    User,
    UserProfile,
    WeeklyReportRecord,
    utcnow,
)


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


def get_recent_messages(session: Session, user_id: str, limit: int = 6) -> list[str]:
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
        select(Message.content)
        .where(Message.session_id.in_(session_ids))
        .order_by(desc(Message.created_at))
        .limit(limit)
    )
    return list(session.execute(stmt).scalars())


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

    recent_excerpt = " | ".join(_safe(msg) for msg in reversed(recent_messages[-3:])) if recent_messages else ""
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


def save_assessment(session: Session, user_id: str, assessment: AssessmentScore) -> AssessmentRecord:
    ensure_user(session, user_id)
    record = AssessmentRecord(
        user_id=user_id,
        assessment_type=assessment.assessment_type,
        score=assessment.score,
        severity_band=assessment.severity_band,
    )
    if isinstance(assessment, AssessmentResult) and assessment.interpretation:
        interp = assessment.interpretation
        record.plain_meaning = interp.plain_meaning
        record.functional_impact = interp.functional_impact
        record.care_consideration = interp.care_consideration
        record.disclaimer = interp.disclaimer
        record.needs_safety_followup = interp.needs_safety_followup
    session.add(record)
    session.commit()
    session.refresh(record)
    return record


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


def save_checkin(session: Session, user_id: str, checkin: DailyCheckin) -> CheckinRecord:
    ensure_user(session, user_id)
    record = CheckinRecord(
        user_id=user_id,
        checkin_date=date.today(),  # noqa: DTZ011
        mood_score=checkin.mood_score,
        anxiety_score=checkin.anxiety_score,
        sleep_hours=checkin.sleep_hours,
        energy_score=checkin.energy_score,
        note=checkin.note or "",
    )
    session.add(record)
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


def save_weekly_report(session: Session, user_id: str, summary: str) -> WeeklyReportRecord:
    record = WeeklyReportRecord(user_id=user_id, summary=summary)
    session.add(record)
    session.commit()
    session.refresh(record)
    return record
