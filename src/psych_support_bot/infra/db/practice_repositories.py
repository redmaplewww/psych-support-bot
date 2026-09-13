"""图内引导练习会话 repository（对话式 54321）。

独立模块（与 exercise_repositories.py 同理由）：练习"会话"是全新状态层，
只放一个模块避免主 repository 继续膨胀。查询全部走 SQLAlchemy ORM
（Query API，绑定参数），与 exercise_repositories.py 的既有写法一致，
无字符串拼接 SQL。UsageEvent 复用既有白名单：
- 建会话（用户 chip 同意须知后）→ exercise_consent
- 每轮步进引导 → exercise_guidance_used（practice_responder 节点内打点）
- 完成落 ExerciseRecord 时 → exercise_completed（save_exercise_record 自带）
"""

from __future__ import annotations

import json
from uuid import uuid4

from sqlalchemy.orm import Session

from psych_support_bot.infra.db.models import PracticeSessionRecord, utcnow
from psych_support_bot.infra.db.repositories import ensure_user, record_usage_event


def create_practice_session(
    session: Session,
    user_id: str,
    exercise_tag: str,
    *,
    disclaimer_version: str = "",
) -> PracticeSessionRecord:
    """开始图内练习会话；同一用户同一练习已有 active 会话则直接复用。"""
    existing = get_active_practice_session(session, user_id, exercise_tag)
    if existing is not None:
        return existing
    bind = session.get_bind()
    from psych_support_bot.infra.db.base import Base

    Base.metadata.create_all(bind=bind)
    ensure_user(session, user_id)
    record = PracticeSessionRecord(
        id=str(uuid4()),
        user_id=user_id,
        exercise_tag=exercise_tag,
        current_step=0,
        step_responses_json="[]",
        guidance_transcript_json="[]",
        status="active",
        disclaimer_version=disclaimer_version,
    )
    session.add(record)
    if disclaimer_version:
        record_usage_event(
            session,
            user_id,
            "exercise_consent",
            exercise_tag=exercise_tag,
            disclaimer_version=disclaimer_version,
            source="chat",
        )
    session.commit()
    session.refresh(record)
    return record


def get_active_practice_session(
    session: Session, user_id: str, exercise_tag: str | None = None
) -> PracticeSessionRecord | None:
    query = (
        session.query(PracticeSessionRecord)  # type: ignore[attr-defined]
        .filter(PracticeSessionRecord.user_id == user_id)  # 绑定参数，非字符串拼接
        .filter(PracticeSessionRecord.status == "active")
    )
    if exercise_tag is not None:
        query = query.filter(PracticeSessionRecord.exercise_tag == exercise_tag)
    return query.order_by(PracticeSessionRecord.updated_at.desc()).first()


def get_paused_practice_session(session: Session, user_id: str, exercise_tag: str) -> PracticeSessionRecord | None:
    return (
        session.query(PracticeSessionRecord)  # type: ignore[attr-defined]
        .filter(PracticeSessionRecord.user_id == user_id)  # 绑定参数，非字符串拼接
        .filter(PracticeSessionRecord.exercise_tag == exercise_tag)
        .filter(PracticeSessionRecord.status == "paused")
        .order_by(PracticeSessionRecord.updated_at.desc())
        .first()
    )


def record_practice_step(
    session: Session,
    session_record: PracticeSessionRecord,
    *,
    user_reply: str,
    guide_reply: str,
) -> PracticeSessionRecord:
    """步进推进：记录用户本步回答 + 引导原文，推进到下一步（等待回答）。

    transcript 条目结构与练习面板 guidance_transcript 约定一致
    （role/content），每轮两条（user + assistant）。
    """
    responses = json.loads(session_record.step_responses_json or "[]")
    responses.append(user_reply)
    transcript = json.loads(session_record.guidance_transcript_json or "[]")
    transcript.append({"role": "user", "content": user_reply})
    if guide_reply:
        transcript.append({"role": "assistant", "content": guide_reply})
    session_record.step_responses_json = json.dumps(responses, ensure_ascii=False)
    session_record.guidance_transcript_json = json.dumps(transcript, ensure_ascii=False)
    session_record.current_step = len(responses)
    session.commit()
    session.refresh(session_record)
    return session_record


def pause_practice_session(session: Session, session_record: PracticeSessionRecord) -> PracticeSessionRecord:
    session_record.status = "paused"
    session.commit()
    session.refresh(session_record)
    return session_record


def resume_practice_session(session: Session, session_record: PracticeSessionRecord) -> PracticeSessionRecord:
    session_record.status = "active"
    session.commit()
    session.refresh(session_record)
    return session_record


def reset_practice_session(session: Session, session_record: PracticeSessionRecord) -> PracticeSessionRecord:
    """重开：清空已答步骤与 transcript，回到第 1 步（等待回答）。"""
    session_record.current_step = 0
    session_record.step_responses_json = "[]"
    session_record.guidance_transcript_json = "[]"
    session_record.status = "active"
    session.commit()
    session.refresh(session_record)
    return session_record


def complete_practice_session(session: Session, session_record: PracticeSessionRecord) -> PracticeSessionRecord:
    session_record.status = "completed"
    session_record.completed_at = utcnow()
    session.commit()
    session.refresh(session_record)
    return session_record
