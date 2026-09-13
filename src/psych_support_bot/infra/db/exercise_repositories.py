"""练习记录 repository（M3）。

独立于 repositories.py：练习落库是全新数据层（此前练习完全不落库），
单放一个模块避免继续膨胀主 repository 文件。全部走 SQLAlchemy ORM
绑定参数（Query API），与既有 repository 的语义一致，无字符串拼接 SQL。
"""

from __future__ import annotations

import json

from sqlalchemy.orm import Session

from psych_support_bot.infra.db.models import ExerciseRecord, utcnow
from psych_support_bot.infra.db.repositories import ensure_user, record_usage_event


def save_exercise_record(
    session: Session,
    user_id: str,
    exercise_tag: str,
    *,
    source: str = "chat",
    reflection_note: str = "",
    step_responses: list[str] | None = None,
    ai_feedback: str = "",
    guidance_transcript: list[dict] | None = None,
    risk_flag: str = "",
) -> ExerciseRecord:
    """练习完成落库（对话图联动 / 页面上报共用）；同时打 exercise_completed 埋点。

    埋点只含动作元数据（tag/source），回答与 AI 反馈只进记录表——伦理边界。
    报告字段（step_responses/ai_feedback/guidance_transcript/risk_flag）由
    页面练习经须知确认后填充；对话内完成（chat）不带报告字段，保持原语义。
    """
    ensure_user(session, user_id)
    record = ExerciseRecord(
        user_id=user_id,
        exercise_tag=exercise_tag,
        source=source,
        reflection_note=reflection_note,
        step_responses_json=json.dumps(step_responses or [], ensure_ascii=False),
        ai_feedback=ai_feedback,
        guidance_transcript_json=json.dumps(guidance_transcript or [], ensure_ascii=False),
        risk_flag=risk_flag if risk_flag in {"elevated", "high", "critical"} else "",
        completed_at=utcnow(),
    )
    session.add(record)
    record_usage_event(session, user_id, "exercise_completed", exercise_tag=exercise_tag, source=source)
    session.commit()
    session.refresh(record)
    return record


def get_exercise_record_by_id(session: Session, user_id: str, record_id: int) -> ExerciseRecord | None:
    """单条练习报告详情（本人可见；user_id 绑定校验防越权）。"""
    return (
        session.query(ExerciseRecord)  # type: ignore[attr-defined]
        .filter(ExerciseRecord.id == record_id)
        .filter(ExerciseRecord.user_id == user_id)
        .first()
    )


def get_user_exercise_records(session: Session, user_id: str, *, limit: int = 50) -> list[ExerciseRecord]:
    return (
        session.query(ExerciseRecord)  # type: ignore[attr-defined]
        .filter(ExerciseRecord.user_id == user_id)  # 绑定参数，非字符串拼接
        .order_by(ExerciseRecord.completed_at.desc())
        .limit(limit)
        .all()
    )
