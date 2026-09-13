"""图内引导练习（对话式 54321）集成测试：完整走聊天链路。

覆盖：提议→确认→5 步→完成落库、暂停→恢复、危机中断自动暂停、
拒绝提议不建会话、elevated 软着陆。LLM 不可用时确定性兜底接住流程
（与生产降级路径一致），故断言聚焦状态机与落库契约而非文案措辞。
"""

import json
from uuid import uuid4

import pytest

from psych_support_bot.ai.practice_flow import PRACTICE_OFFER_MARKER
from psych_support_bot.ai.schemas.messages import ConversationRequest
from psych_support_bot.infra.db.exercise_repositories import get_user_exercise_records
from psych_support_bot.infra.db.init_db import init_db
from psych_support_bot.infra.db.practice_repositories import (
    get_active_practice_session,
    get_paused_practice_session,
)
from psych_support_bot.infra.db.session import SessionLocal
from psych_support_bot.services.conversation import conversation_service

init_db()


@pytest.fixture(autouse=True)
def _rules_only_risk_classification(monkeypatch):
    """练习流测试断言的是状态机路由与落库契约，不测语义风险模型。

    全量套件下真实 LLM 语义分类存在随机漂移（evals 巡检实证：中性短句
    偶被抬到 elevated），会把「同意并开始」这类练习确认语随机推进软着陆
    暂停，污染路由断言。此处固定走规则层（确定性），与生产 fail-safe
    语义一致：LLM 不可用时维持规则判定。
    """
    monkeypatch.setattr(
        "psych_support_bot.ai.safety.llm_classifier.classify_risk_llm",
        lambda *args, **kwargs: (None, None),
    )


def _walk_consent(user_id: str) -> str:
    """提议 → 确认，返回 session_id；断言提议轮契约。"""
    with SessionLocal() as session:
        offer = conversation_service.respond(
            ConversationRequest(user_id=user_id, message="带我做个54321"), session=session
        )
    assert offer.debug.get("source") == "practice_guide"
    assert offer.debug.get("practice_action") == "offer"
    assert offer.question_options and offer.question_options[0]["send"] == PRACTICE_OFFER_MARKER
    assert "不是医疗诊断或治疗" in offer.reply.text  # 须知先于确认时刻

    with SessionLocal() as session:
        start = conversation_service.respond(
            ConversationRequest(user_id=user_id, session_id=offer.session_id, message=PRACTICE_OFFER_MARKER),
            session=session,
        )
    assert start.debug.get("practice_action") == "start"
    assert start.question_options and start.question_options[0]["send"] == "先停一下"
    with SessionLocal() as session:
        record = get_active_practice_session(session, user_id)
    assert record is not None and record.status == "active" and record.current_step == 0
    return offer.session_id


def test_full_54321_walkthrough_creates_exercise_record() -> None:
    user_id = f"practice-full-{uuid4().hex[:8]}"
    session_id = _walk_consent(user_id)

    answers = [
        "窗外的树、桌上的杯子、台灯、地毯、我的手机",
        "椅子的扶手、我的毛衣、桌面",
        "空调声、街上的车声",
        "咖啡香、书页的味道",
        "嘴里有牙膏味",
    ]
    final_debug = None
    with SessionLocal() as session:
        for i, answer in enumerate(answers):
            resp = conversation_service.respond(
                ConversationRequest(user_id=user_id, session_id=session_id, message=answer), session=session
            )
            final_debug = resp.debug
            expected_action = "complete" if i == len(answers) - 1 else "advance"
            assert resp.debug.get("practice_action") == expected_action, f"turn {i + 1}"
            assert resp.debug.get("source") == "practice_guide"
            session_id = resp.session_id

    assert final_debug.get("practice_step") == 4
    with SessionLocal() as session:
        # 会话完成 + 练习记录落库（guidance_transcript 首次由对话路径填充）
        assert get_active_practice_session(session, user_id) is None
        records = get_user_exercise_records(session, user_id)
        assert len(records) == 1
        record = records[0]
        assert record.exercise_tag == "panic_grounding_5_4_3_2_1"
        assert record.source == "chat"
        responses = json.loads(record.step_responses_json or "[]")
        transcript = json.loads(record.guidance_transcript_json or "[]")
        assert responses == answers
        assert len(transcript) >= 10  # 每步 user+assistant 两条
        assert record.ai_feedback  # 收尾语即反馈
        assert record.completed_at is not None


def test_pause_then_resume_mid_practice() -> None:
    user_id = f"practice-pause-{uuid4().hex[:8]}"
    session_id = _walk_consent(user_id)
    with SessionLocal() as session:
        resp = conversation_service.respond(
            ConversationRequest(user_id=user_id, session_id=session_id, message="先停一下"), session=session
        )
        assert resp.debug.get("practice_action") == "pause"
        session_id = resp.session_id
    with SessionLocal() as session:
        assert get_active_practice_session(session, user_id) is None
        paused = get_paused_practice_session(session, user_id, "panic_grounding_5_4_3_2_1")
        assert paused is not None

    # 暂停中的会话不劫持普通聊天
    with SessionLocal() as session:
        chat = conversation_service.respond(
            ConversationRequest(user_id=user_id, session_id=session_id, message="我今天上班好累"), session=session
        )
        assert chat.debug.get("practice_action") in (None, "")

    # 说「继续」恢复，从当前步接着来（不重开）
    with SessionLocal() as session:
        resume = conversation_service.respond(
            ConversationRequest(user_id=user_id, session_id=session_id, message="继续"), session=session
        )
        assert resume.debug.get("practice_action") == "resume"
        session_id = resume.session_id
        assert get_active_practice_session(session, user_id) is not None
        # 完成剩余步骤（第 1 步未答，需 5 轮）
        for answer in ("树、杯子、台灯、地毯、手机", "扶手、毛衣", "空调声", "咖啡香", "牙膏味"):
            resp = conversation_service.respond(
                ConversationRequest(user_id=user_id, session_id=session_id, message=answer), session=session
            )
            session_id = resp.session_id
    with SessionLocal() as session:
        assert get_user_exercise_records(session, user_id), "恢复后走完应落练习记录"


def test_crisis_mid_practice_pauses_session() -> None:
    user_id = f"practice-crisis-{uuid4().hex[:8]}"
    session_id = _walk_consent(user_id)
    with SessionLocal() as session:
        conversation_service.respond(
            ConversationRequest(user_id=user_id, session_id=session_id, message="树、杯子、台灯、地毯、手机"),
            session=session,
        )
        crisis = conversation_service.respond(
            ConversationRequest(user_id=user_id, session_id=session_id, message="我突然想到我不想活了"), session=session
        )
        assert crisis.mode == "crisis"
        assert crisis.risk.risk_level in {"high", "critical"}
        # 危机轮不进练习分支：无练习 chips，不继续推步骤
        assert all(o.get("send") != "先停一下" for o in crisis.question_options)
        session_id = crisis.session_id
    with SessionLocal() as session:
        assert get_active_practice_session(session, user_id) is None
        assert get_paused_practice_session(session, user_id, "panic_grounding_5_4_3_2_1") is not None


def test_decline_offer_creates_no_session() -> None:
    user_id = f"practice-decline-{uuid4().hex[:8]}"
    with SessionLocal() as session:
        offer = conversation_service.respond(
            ConversationRequest(user_id=user_id, message="带我做个54321"), session=session
        )
        assert offer.debug.get("practice_action") == "offer"
        decline = conversation_service.respond(
            ConversationRequest(user_id=user_id, session_id=offer.session_id, message="算了，不想做了"), session=session
        )
        # 拒绝轮放行主链，不建会话、不出步骤
        assert decline.debug.get("practice_action") in (None, "")
    with SessionLocal() as session:
        assert get_active_practice_session(session, user_id) is None
        assert get_paused_practice_session(session, user_id, "panic_grounding_5_4_3_2_1") is None


def test_repeated_consent_is_idempotent() -> None:
    user_id = f"practice-idem-{uuid4().hex[:8]}"
    session_id = _walk_consent(user_id)
    with SessionLocal() as session:
        again = conversation_service.respond(
            ConversationRequest(user_id=user_id, session_id=session_id, message=PRACTICE_OFFER_MARKER), session=session
        )
        # 已有 active 会话：重复确认按 continue 处理，不重复建会话
        assert again.debug.get("practice_action") == "advance"
        session_id = again.session_id
    with SessionLocal() as session:
        active = get_active_practice_session(session, user_id)
        assert active is not None
        assert len(json.loads(active.step_responses_json)) == 1  # 第一次回答已记录
