"""Integration regression: emotional disclosure mid-questionnaire pauses the
quiz instead of pressing for the next numeric answer (Langfuse 巡检 2026-09-04:
「我最近还感到很焦虑」被反复回以「请回复一个数字」)."""

from uuid import uuid4

from psych_support_bot.ai.schemas.messages import ConversationRequest
from psych_support_bot.infra.db.init_db import init_db
from psych_support_bot.infra.db.repositories import get_paused_questionnaire_session
from psych_support_bot.infra.db.session import SessionLocal
from psych_support_bot.services.conversation import conversation_service

init_db()


def test_mid_questionnaire_emotional_disclosure_pauses() -> None:
    user_id = f"assessment-emotional-{uuid4()}"
    with SessionLocal() as session:
        conversation_service.respond(
            ConversationRequest(user_id=user_id, message="我想做 PHQ-9"),
            session=session,
        )
        conversation_service.respond(
            ConversationRequest(user_id=user_id, message="1"),
            session=session,
        )
        resp = conversation_service.respond(
            ConversationRequest(user_id=user_id, message="我最近还感到很焦虑"),
            session=session,
        )

        assert resp.debug["source"] == "questionnaire_emotional_pause"
        assert resp.mode == "support"
        assert "先放一放" in resp.reply.text
        # 进度必须保存（paused 而非丢弃）
        assert get_paused_questionnaire_session(session, user_id, "phq9") is not None


def test_mid_questionnaire_numeric_answer_still_scores() -> None:
    """严格解析不得误伤正常作答：数字与「N分」仍然计分。"""
    user_id = f"assessment-numeric-{uuid4()}"
    with SessionLocal() as session:
        conversation_service.respond(
            ConversationRequest(user_id=user_id, message="我想做 PHQ-9"),
            session=session,
        )
        resp = conversation_service.respond(
            ConversationRequest(user_id=user_id, message="2分"),
            session=session,
        )
        assert resp.debug["source"] == "questionnaire_progress"


def test_mid_questionnaire_crisis_disclosure_escalates() -> None:
    user_id = f"assessment-crisis-{uuid4()}"
    with SessionLocal() as session:
        conversation_service.respond(
            ConversationRequest(user_id=user_id, message="我想做 PHQ-9"),
            session=session,
        )
        resp = conversation_service.respond(
            ConversationRequest(user_id=user_id, message="我不想活了，撑不住了"),
            session=session,
        )

    assert resp.mode == "crisis"
    assert resp.risk.needs_crisis_mode is True
    assert resp.debug["source"] == "questionnaire_emotional_pause"
