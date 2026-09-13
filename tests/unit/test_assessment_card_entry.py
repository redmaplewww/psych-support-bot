"""聊天入口卡片化（assessment_card）回归测试。

新契约：聊天里说「我想做 XX 量表」只发引导卡——不建会话、不出题、
不带选项 chips；会话由评估页确认须知后创建。聊天侧保留的状态机路径
（进行中作答/暂停续答/情绪倾诉暂停/危机升级/退出确认）以页面预建会话
为前置，在集成层覆盖（test_conversation_flow / test_questionnaire_emotional_pause）。
"""

from uuid import uuid4

from psych_support_bot.ai.schemas.messages import ConversationRequest
from psych_support_bot.infra.db.init_db import init_db
from psych_support_bot.infra.db.repositories import (
    create_questionnaire_session,
    get_active_questionnaire_session,
    get_paused_questionnaire_session,
)
from psych_support_bot.infra.db.session import SessionLocal
from psych_support_bot.services.conversation import conversation_service

init_db()


def test_card_request_creates_no_session_and_no_chips() -> None:
    user_id = f"u_card_{uuid4().hex[:8]}"
    with SessionLocal() as session:
        resp = conversation_service.respond(
            ConversationRequest(user_id=user_id, message="我想做个焦虑量表"),
            session=session,
        )
        assert resp.mode == "assessment"
        assert resp.debug["source"] == "assessment_card"
        assert resp.debug["llm_used"] is False
        assert resp.debug["assessment_type"] == "gad7"
        assert resp.question_options == []
        assert get_active_questionnaire_session(session, user_id) is None
        assert get_paused_questionnaire_session(session, user_id, "gad7") is None
        # 引导文案含量表名与时长提示，不携带题干/选项串
        assert "GAD-7" in resp.reply.text
        assert "（0=" not in resp.reply.text


def test_card_request_in_english() -> None:
    user_id = f"u_card_{uuid4().hex[:8]}"
    with SessionLocal() as session:
        resp = conversation_service.respond(
            ConversationRequest(user_id=user_id, message="I want to take PHQ-9"),
            session=session,
        )
        assert resp.debug["source"] == "assessment_card"
        assert resp.debug["assessment_type"] == "phq9"
        assert "PHQ-9" in resp.reply.text


def test_panel_created_session_still_answers_via_chat() -> None:
    """页面预建会话后，聊天数字作答仍推进（双轨共用的会话层契约）。"""
    user_id = f"u_card_{uuid4().hex[:8]}"
    with SessionLocal() as session:
        create_questionnaire_session(session, user_id, "gad7")
        step1 = conversation_service.respond(
            ConversationRequest(user_id=user_id, message="2"),
            session=session,
        )
        assert step1.debug["source"] == "questionnaire_progress"
        assert step1.question_options  # 续答轮保留 chips
        assert get_active_questionnaire_session(session, user_id) is not None
