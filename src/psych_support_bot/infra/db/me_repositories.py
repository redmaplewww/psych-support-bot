"""「我」页数据层：全量导出 / 清空历史记录 / 注销账号。

删除语义（隐私协议承诺的删除权）：
- clear_user_records 只删主动记录（测评/练习/打卡），保留聊天与账号本身；
- delete_user_account 级联删除该用户在全部业务表中的行——messages 按
  sessions.user_id 间接归属，需先删消息再删会话。
所有删除都是逐表显式 ORM 语句（user_id 走绑定参数），返回逐表行数，
由路由层记 usage_events 审计后一并提交。
"""

import json
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from psych_support_bot.infra.db.models import (
    AssessmentRecord,
    CheckinRecord,
    ConversationSession,
    ExerciseRecord,
    Message,
    PlanEnrollment,
    QuestionnaireSessionRecord,
    RiskEvent,
    UsageEvent,
    User,
    UserCredential,
    UserProfile,
    WeeklyReportRecord,
)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def export_user_data(session: Session, user_id: str) -> dict:
    """全量导出：用户在此产品写下与生成的一切，JSON 一次带回。"""
    user = session.get(User, user_id)
    sessions = (
        session.query(ConversationSession)
        .filter(ConversationSession.user_id == user_id)
        .order_by(ConversationSession.created_at.asc())
        .all()
    )
    messages: list[dict] = []
    for conv in sessions:
        for msg in (
            session.query(Message)
            .filter(Message.session_id == conv.id)
            .order_by(Message.created_at.asc(), Message.id.asc())
            .all()
        ):
            messages.append(
                {
                    "session_id": conv.id,
                    "role": msg.role,
                    "content": msg.content,
                    "safety_flag": msg.safety_flag,
                    "created_at": _iso(msg.created_at),
                }
            )

    return {
        "user": {
            "user_id": user_id,
            "created_at": _iso(user.created_at) if user else None,
        },
        "profile": _export_profile(session, user_id),
        "assessments": [
            {
                "assessment_type": r.assessment_type,
                "score": r.score,
                "severity_band": r.severity_band,
                "source": r.source,
                "needs_safety_followup": r.needs_safety_followup,
                "created_at": _iso(r.created_at),
            }
            for r in session.query(AssessmentRecord)
            .filter(AssessmentRecord.user_id == user_id)
            .order_by(AssessmentRecord.created_at.asc())
            .all()
        ],
        "exercise_records": [
            {
                "exercise_tag": r.exercise_tag,
                "source": r.source,
                "reflection_note": r.reflection_note,
                "step_responses": _load_json_array(r.step_responses_json),
                "ai_feedback": r.ai_feedback,
                "risk_flag": r.risk_flag,
                "completed_at": _iso(r.completed_at),
            }
            for r in session.query(ExerciseRecord)
            .filter(ExerciseRecord.user_id == user_id)
            .order_by(ExerciseRecord.completed_at.asc())
            .all()
        ],
        "checkins": [
            {
                "date": r.checkin_date.isoformat(),
                "mood_score": r.mood_score,
                "anxiety_score": r.anxiety_score,
                "sleep_hours": r.sleep_hours,
                "energy_score": r.energy_score,
                "note": r.note,
                "created_at": _iso(r.created_at),
            }
            for r in session.query(CheckinRecord)
            .filter(CheckinRecord.user_id == user_id)
            .order_by(CheckinRecord.checkin_date.asc())
            .all()
        ],
        "questionnaire_sessions": [
            {
                "session_id": r.id,
                "assessment_type": r.assessment_type,
                "answers": _load_json_array(r.answers_json),
                "status": r.status,
                "source": r.source,
                "created_at": _iso(r.created_at),
                "completed_at": _iso(r.completed_at),
            }
            for r in session.query(QuestionnaireSessionRecord)
            .filter(QuestionnaireSessionRecord.user_id == user_id)
            .all()
        ],
        "conversations": [
            {
                "session_id": conv.id,
                "mode": conv.mode,
                "risk_level": conv.risk_level,
                "summary": conv.summary,
                "created_at": _iso(conv.created_at),
            }
            for conv in sessions
        ],
        "messages": messages,
        "risk_events": [
            {
                "risk_level": r.risk_level,
                "risk_reason": r.risk_reason,
                "created_at": _iso(r.created_at),
            }
            for r in session.query(RiskEvent).filter(RiskEvent.user_id == user_id).all()
        ],
        "weekly_reports": [
            {"summary": r.summary, "created_at": _iso(r.created_at)}
            for r in session.query(WeeklyReportRecord).filter(WeeklyReportRecord.user_id == user_id).all()
        ],
        "plan_enrollments": [
            {
                "plan_id": r.plan_id,
                "status": r.status,
                "current_day": r.current_day,
                "enrolled_at": _iso(r.enrolled_at),
            }
            for r in session.query(PlanEnrollment).filter(PlanEnrollment.user_id == user_id).all()
        ],
        "exported_at": datetime.now(UTC).isoformat(),
    }


def _export_profile(session: Session, user_id: str) -> dict:
    profile = session.get(UserProfile, user_id)
    if profile is None:
        return {}
    return {
        "display_name": profile.display_name,
        "primary_concerns": profile.primary_concerns,
        "goals": profile.goals,
        "support_preferences": profile.support_preferences,
        "risk_notes": profile.risk_notes,
        "updated_at": _iso(profile.updated_at),
    }


def _load_json_array(raw: str) -> list:
    try:
        value = json.loads(raw) if raw else []
        return value if isinstance(value, list) else []
    except (TypeError, ValueError):
        return []


def me_summary(session: Session, user_id: str) -> dict:
    """「我」页头部：账号事实 + 最近 30 天三类动作计数（只数次数，不读内容）。"""
    user = session.get(User, user_id)
    now = datetime.now(UTC)
    cutoff_dt = now - timedelta(days=30)  # DateTime 列（assessments/exercises）
    cutoff_date = now.date() - timedelta(days=30)  # Date 列（checkins）
    return {
        "user_id": user_id,
        "created_at": _iso(user.created_at) if user else None,
        "counts_30d": {
            "assessments": int(
                session.query(AssessmentRecord)
                .filter(AssessmentRecord.user_id == user_id)
                .filter(AssessmentRecord.created_at >= cutoff_dt)
                .count()
            ),
            "exercises": int(
                session.query(ExerciseRecord)
                .filter(ExerciseRecord.user_id == user_id)
                .filter(ExerciseRecord.completed_at >= cutoff_dt)
                .count()
            ),
            "checkins": int(
                session.query(CheckinRecord)
                .filter(CheckinRecord.user_id == user_id)
                .filter(CheckinRecord.checkin_date >= cutoff_date)
                .count()
            ),
        },
    }


def clear_user_records(session: Session, user_id: str) -> dict[str, int]:
    """清空主动记录（测评/练习/打卡），保留聊天与账号。返回逐表删除行数。"""
    counts = {
        "assessments": session.query(AssessmentRecord)
        .filter(AssessmentRecord.user_id == user_id)
        .delete(synchronize_session=False),
        "exercise_records": session.query(ExerciseRecord)
        .filter(ExerciseRecord.user_id == user_id)
        .delete(synchronize_session=False),
        "checkins": session.query(CheckinRecord)
        .filter(CheckinRecord.user_id == user_id)
        .delete(synchronize_session=False),
    }
    return {key: int(value) for key, value in counts.items()}


def delete_user_account(session: Session, user_id: str) -> dict[str, int]:
    """注销：级联删除该用户全部数据（含聊天与账号行）。返回逐表删除行数。"""
    counts: dict[str, int] = {}

    # messages 按 sessions.user_id 间接归属：先收集会话 id，删消息再删会话。
    session_ids = [
        row[0] for row in session.query(ConversationSession.id).filter(ConversationSession.user_id == user_id).all()
    ]
    if session_ids:
        counts["messages"] = int(
            session.query(Message).filter(Message.session_id.in_(session_ids)).delete(synchronize_session=False)
        )
    else:
        counts["messages"] = 0

    counts["sessions"] = int(
        session.query(ConversationSession)
        .filter(ConversationSession.user_id == user_id)
        .delete(synchronize_session=False)
    )
    counts["questionnaire_sessions"] = int(
        session.query(QuestionnaireSessionRecord)
        .filter(QuestionnaireSessionRecord.user_id == user_id)
        .delete(synchronize_session=False)
    )
    counts["assessments"] = int(
        session.query(AssessmentRecord).filter(AssessmentRecord.user_id == user_id).delete(synchronize_session=False)
    )
    counts["exercise_records"] = int(
        session.query(ExerciseRecord).filter(ExerciseRecord.user_id == user_id).delete(synchronize_session=False)
    )
    counts["checkins"] = int(
        session.query(CheckinRecord).filter(CheckinRecord.user_id == user_id).delete(synchronize_session=False)
    )
    counts["risk_events"] = int(
        session.query(RiskEvent).filter(RiskEvent.user_id == user_id).delete(synchronize_session=False)
    )
    counts["weekly_reports"] = int(
        session.query(WeeklyReportRecord)
        .filter(WeeklyReportRecord.user_id == user_id)
        .delete(synchronize_session=False)
    )
    counts["plan_enrollments"] = int(
        session.query(PlanEnrollment).filter(PlanEnrollment.user_id == user_id).delete(synchronize_session=False)
    )
    counts["usage_events"] = int(
        session.query(UsageEvent).filter(UsageEvent.user_id == user_id).delete(synchronize_session=False)
    )
    counts["user_profiles"] = int(
        session.query(UserProfile).filter(UserProfile.user_id == user_id).delete(synchronize_session=False)
    )
    counts["user_credentials"] = int(
        session.query(UserCredential).filter(UserCredential.user_id == user_id).delete(synchronize_session=False)
    )
    counts["users"] = int(session.query(User).filter(User.id == user_id).delete(synchronize_session=False))
    return counts
