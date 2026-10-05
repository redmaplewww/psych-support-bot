"""图内引导练习（对话式 54321）单测。

覆盖：意图分类（继续/暂停/恢复/重开/提议/确认/拒绝）、确定性文案、
步进/收尾生成兜底、repository CRUD、GraphState 路由字段。
LLM 调用全部走确定性 fallback（mock 或环境关闭），不采样。
"""

import json
from uuid import uuid4

from psych_support_bot.ai.practice_flow import (
    PRACTICE_OFFER_MARKER,
    PRACTICE_TAG,
    build_elevated_pause_reply,
    build_offer_reply,
    build_pause_reply,
    build_resume_reply,
    build_start_reply,
    detect_practice_intent,
    generate_completion_reply,
    generate_step_reply,
    split_practice_bubbles,
)
from psych_support_bot.ai.schemas.messages import RiskResult
from psych_support_bot.infra.db.practice_repositories import (
    complete_practice_session,
    create_practice_session,
    get_active_practice_session,
    get_paused_practice_session,
    pause_practice_session,
    record_practice_step,
    reset_practice_session,
)
from psych_support_bot.infra.db.session import SessionLocal


def _risk(level: str = "low") -> RiskResult:
    return RiskResult(risk_level=level, risk_types=[], needs_crisis_mode=level in {"high", "critical"}, reason="")


# ---------------------------------------------------------------------------
# 意图分类
# ---------------------------------------------------------------------------


def test_entry_intent_matches_54321_and_grounding() -> None:
    for message in ("带我做个54321", "我想做接地练习", "let's do the grounding exercise", "5-4-3-2-1"):
        intent = detect_practice_intent(message, active_practice=None, risk_result=_risk())
        assert intent.kind == "offer", message


def test_consent_with_offer_pending() -> None:
    intent = detect_practice_intent(
        PRACTICE_OFFER_MARKER,
        active_practice=None,
        risk_result=_risk(),
        last_bot_reply=f"准备好了就点「{PRACTICE_OFFER_MARKER}」。",
    )
    assert intent.kind == "consent"


def test_consent_without_offer_pending_does_not_hijack() -> None:
    """无提议挂起时，普通「好/可以」不触发练习开始。"""
    intent = detect_practice_intent("好的", active_practice=None, risk_result=_risk(), last_bot_reply="聊点别的吧")
    assert intent.kind == "none"


def test_active_practice_normal_answer_is_continue() -> None:
    practice = {"status": "active", "step": 0, "responses": [], "transcript": []}
    intent = detect_practice_intent("窗外的树、桌子、杯子", active_practice=practice, risk_result=_risk())
    assert intent.kind == "continue"


def test_active_practice_pause_keywords() -> None:
    practice = {"status": "active", "step": 1, "responses": ["a"], "transcript": []}
    for message in ("先停一下", "暂停", "让我静静", "not now"):
        intent = detect_practice_intent(message, active_practice=practice, risk_result=_risk())
        assert intent.kind == "pause", message


def test_paused_practice_resume_and_restart() -> None:
    practice = {"status": "paused", "step": 2, "responses": ["a", "b"], "transcript": []}
    assert detect_practice_intent("继续", active_practice=practice, risk_result=_risk()).kind == "resume"
    assert detect_practice_intent("接着练", active_practice=practice, risk_result=_risk()).kind == "resume"
    assert detect_practice_intent("重新开始", active_practice=practice, risk_result=_risk()).kind == "restart"


def test_paused_practice_ignores_plain_chat() -> None:
    """暂停中的会话不劫持普通聊天。"""
    practice = {"status": "paused", "step": 2, "responses": ["a"], "transcript": []}
    intent = detect_practice_intent("我今天上班好累", active_practice=practice, risk_result=_risk())
    assert intent.kind == "none"


# ---------------------------------------------------------------------------
# 确定性文案
# ---------------------------------------------------------------------------


def test_offer_reply_contains_disclaimer_and_marker() -> None:
    text, options = build_offer_reply("zh")
    assert "5-4-3-2-1" in text
    assert "不是医疗诊断或治疗" in text
    assert PRACTICE_OFFER_MARKER in text
    assert options and options[0]["send"] == PRACTICE_OFFER_MARKER


def test_start_reply_contains_first_step() -> None:
    text = build_start_reply("zh")
    assert "5" in text and "看到" in text


def test_pause_and_resume_replies() -> None:
    pause = build_pause_reply("zh", completed_steps=2)
    assert "2 步" in pause and "继续" in pause
    resume = build_resume_reply("zh", next_index=1)
    assert "触摸" in resume or "摸" in resume


def test_elevated_pause_reply_is_soft_landing() -> None:
    reply = build_elevated_pause_reply("zh")
    assert "练习先放到一边" in reply
    assert "5" not in reply  # 不继续推步骤


def test_split_practice_bubbles_cap() -> None:
    text = "\n\n".join(f"段落{i}" for i in range(5))
    bubbles = split_practice_bubbles(text)
    assert len(bubbles) == 3
    assert "段落3" in bubbles[2] and "段落4" in bubbles[2]


# ---------------------------------------------------------------------------
# LLM 生成兜底（不发真实采样断言文案稳定性，只断言结构）
# ---------------------------------------------------------------------------


def test_generate_step_reply_fallback_on_llm_failure(monkeypatch) -> None:
    def _boom(*args, **kwargs):
        raise RuntimeError("llm down")

    monkeypatch.setattr("psych_support_bot.ai.practice_flow._invoke", _boom)
    state = {"active_practice": {"transcript": []}}
    reply = generate_step_reply(state, step_index=0, user_reply="树、杯子", expected_language="zh")
    assert "触" in reply or "摸" in reply  # 回退到第 2 步指令


def test_generate_completion_reply_fallback_on_llm_failure(monkeypatch) -> None:
    def _boom(*args, **kwargs):
        raise RuntimeError("llm down")

    monkeypatch.setattr("psych_support_bot.ai.practice_flow._invoke", _boom)
    state = {"active_practice": {"transcript": [{"role": "user", "content": "树"}]}}
    reply = generate_completion_reply(state, expected_language="zh")
    assert "五步都走完了" in reply


# ---------------------------------------------------------------------------
# Repository
# ---------------------------------------------------------------------------


def test_practice_session_crud_roundtrip() -> None:
    user_id = f"practice-crud-{uuid4().hex[:8]}"
    with SessionLocal() as session:
        record = create_practice_session(session, user_id, PRACTICE_TAG, disclaimer_version="test-v1")
        assert record.status == "active"
        assert record.current_step == 0

        # 幂等：重复创建返回同一会话
        again = create_practice_session(session, user_id, PRACTICE_TAG)
        assert again.id == record.id

        record_practice_step(session, record, user_reply="树、杯子", guide_reply="好，接住了。")
        record_practice_step(session, record, user_reply="扶手、毛衣", guide_reply="很好。")
        session.refresh(record)
        assert record.current_step == 2
        responses = json.loads(record.step_responses_json)
        assert responses == ["树、杯子", "扶手、毛衣"]
        transcript = json.loads(record.guidance_transcript_json)
        assert len(transcript) == 4  # 每轮 user+assistant 两条

        pause_practice_session(session, record)
        assert get_active_practice_session(session, user_id) is None
        paused = get_paused_practice_session(session, user_id, PRACTICE_TAG)
        assert paused is not None and paused.current_step == 2

        reset_practice_session(session, paused)
        assert paused.current_step == 0
        assert json.loads(paused.step_responses_json) == []


def test_complete_practice_session_sets_timestamp() -> None:
    user_id = f"practice-done-{uuid4().hex[:8]}"
    with SessionLocal() as session:
        record = create_practice_session(session, user_id, PRACTICE_TAG)
        complete_practice_session(session, record)
        assert record.status == "completed"
        assert record.completed_at is not None
        assert get_active_practice_session(session, user_id) is None
