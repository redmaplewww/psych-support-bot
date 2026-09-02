"""D1: Unit tests for P0 fixes that were missing coverage.

Covers:
- P0-6: high-risk LLM failure falls back to crisis reply
- P0-16: get_temperature_for_mode returns correct temperatures
- P0-5: diagnosis keywords intercepted to support mode
- P0-3: safety_reviewer diagnosis/overreach/leak sanitization
- crisis.py: build_crisis_reply Chinese and English output
"""

from typing import cast

from psych_support_bot.ai.nodes.response_generator import generate_response
from psych_support_bot.ai.nodes.safety_reviewer import review_response
from psych_support_bot.ai.routers.intent import detect_mode
from psych_support_bot.ai.safety.crisis import build_crisis_reply
from psych_support_bot.ai.safety.rules import classify_message_risk
from psych_support_bot.ai.schemas.messages import (
    GeneratedReply,
    RiskResult,
)
from psych_support_bot.ai.schemas.state import GraphState
from psych_support_bot.infra.llm.factory import (
    DEFAULT_TEMPERATURE,
    MODE_TEMPERATURES,
    get_temperature_for_mode,
)

# ---------------------------------------------------------------------------
# P0-6: High-risk LLM failure falls back to crisis reply
# ---------------------------------------------------------------------------


def _build_state(
    *,
    mode: str = "support",
    user_message: str = "I feel stressed",
    risk_level: str = "low",
) -> GraphState:
    return cast(
        GraphState,
        {
            "user_id": "test-user",
            "session_id": "session-1",
            "user_message": user_message,
            "memory_summary": "",
            "knowledge_context": "",
            "mode": mode,
            "risk_result": RiskResult(
                risk_level=risk_level,
                risk_types=[],
                needs_crisis_mode=risk_level in {"high", "critical"},
                reason="test",
            ),
            "generated_reply": GeneratedReply(
                text="",
                style=mode,
                includes_action_step=True,
            ),
            "session_summary": "",
            "topics": [],
            "fallback_used": False,
            "consultation_required": False,
            "consultation_agents": [],
            "consultation_notes": "",
            "consultation_opinions": [],
            "interview_stage": "engagement",
            "question_strategy": "open",
            "challenge_allowed": False,
            "loop_hint": "Start broad.",
            "expected_language": "zh" if any("\u4e00" <= c <= "\u9fff" for c in user_message) else "en",
        },
    )


def test_high_risk_llm_failure_falls_back_to_crisis_reply(monkeypatch) -> None:
    """P0-6: When LLM fails for high-risk crisis, fallback to crisis template."""
    monkeypatch.setattr(
        "psych_support_bot.ai.nodes.response_generator.generate_clinically_bounded_reply",
        lambda **_: (_ for _ in ()).throw(RuntimeError("llm unavailable")),
    )
    state = _build_state(
        mode="crisis",
        user_message="我想自杀",
        risk_level="high",
    )
    result = generate_response(state)
    assert result["fallback_used"] is True
    assert "安全" in result["generated_reply"].text or "safety" in result["generated_reply"].text.lower()


def test_high_risk_llm_success_does_not_set_fallback(monkeypatch) -> None:
    """P0-6: When LLM succeeds for high-risk, fallback_used stays False."""
    monkeypatch.setattr(
        "psych_support_bot.ai.nodes.response_generator.generate_clinically_bounded_reply",
        lambda **_: "共情回复，我在这里陪你。",
    )
    state = _build_state(
        mode="crisis",
        user_message="我很痛苦",
        risk_level="high",
    )
    result = generate_response(state)
    assert result["fallback_used"] is False
    assert result["generated_reply"].text == "共情回复，我在这里陪你。"


# ---------------------------------------------------------------------------
# P0-16: Temperature per mode
# ---------------------------------------------------------------------------


def test_crisis_temperature_is_zero() -> None:
    """P0-16: Crisis mode must use temperature 0.0 for deterministic output."""
    assert get_temperature_for_mode("crisis") == 0.0


def test_assessment_temperature_is_low() -> None:
    """P0-16: Assessment mode uses low temperature for consistent guidance."""
    assert get_temperature_for_mode("assessment") == 0.3


def test_support_temperature_is_moderate() -> None:
    """P0-16: Support mode uses moderate temperature for natural empathy."""
    assert get_temperature_for_mode("support") == 0.4


def test_intervention_temperature_is_highest() -> None:
    """P0-16: Intervention mode uses highest temperature for creative suggestions."""
    assert get_temperature_for_mode("intervention") == 0.5


def test_unknown_mode_uses_default() -> None:
    """P0-16: Unknown mode falls back to default temperature."""
    assert get_temperature_for_mode("unknown_mode") == DEFAULT_TEMPERATURE


def test_all_modes_have_temperatures() -> None:
    """P0-16: All defined conversation modes have temperature entries."""
    expected_modes = {"crisis", "assessment", "support", "planning", "intervention"}
    assert expected_modes.issubset(set(MODE_TEMPERATURES.keys()))


def test_same_message_different_mode_different_temperature() -> None:
    """P0-16: Same message in different modes gets different temperatures."""
    t_crisis = get_temperature_for_mode("crisis")
    t_support = get_temperature_for_mode("support")
    t_intervention = get_temperature_for_mode("intervention")
    assert t_crisis != t_support
    assert t_support != t_intervention


# ---------------------------------------------------------------------------
# P0-5: Diagnosis keyword interception
# ---------------------------------------------------------------------------


def test_chinese_diagnosis_intercepted_to_support() -> None:
    """P0-5: '我是不是抑郁症' should route to support, not assessment."""
    assert detect_mode("我是不是抑郁症") == "support"


def test_chinese_diagnosis_anxiety_intercepted() -> None:
    """P0-5: '我是不是焦虑症' should route to support."""
    assert detect_mode("我是不是焦虑症") == "support"


def test_chinese_diagnosis_bipolar_intercepted() -> None:
    """P0-5: '我是不是双相' should route to support."""
    assert detect_mode("我是不是双相") == "support"


def test_english_diagnosis_intercepted_to_support() -> None:
    """P0-5: English 'am I depressed' should route to support."""
    assert detect_mode("am I depressed") == "support"


def test_english_diagnosis_adhd_intercepted() -> None:
    """P0-5: English 'do I have ADHD' should route to support."""
    assert detect_mode("do I have ADHD") == "support"


def test_diagnosis_does_not_block_assessment() -> None:
    """P0-5: Assessment request without diagnosis keywords should still route to assessment."""
    assert detect_mode("我想做焦虑量表测评") == "assessment"


# ---------------------------------------------------------------------------
# P0-3: Safety reviewer diagnosis/overreach/leak sanitization
# ---------------------------------------------------------------------------


def _build_review_state(
    *,
    reply_text: str,
    user_message: str = "我最近很难受",
    challenge_allowed: bool = False,
) -> GraphState:
    return cast(
        GraphState,
        {
            "user_id": "test-user",
            "session_id": "session-1",
            "user_message": user_message,
            "memory_summary": "",
            "knowledge_context": "",
            "mode": "support",
            "risk_result": RiskResult(
                risk_level="low",
                risk_types=[],
                needs_crisis_mode=False,
                reason="test",
            ),
            "generated_reply": GeneratedReply(
                text=reply_text,
                style="support",
                includes_action_step=True,
            ),
            "session_summary": "",
            "topics": [],
            "fallback_used": False,
            "consultation_required": False,
            "consultation_agents": [],
            "consultation_notes": "",
            "consultation_opinions": [],
            "interview_stage": "engagement",
            "question_strategy": "open",
            "challenge_allowed": challenge_allowed,
            "loop_hint": "Start broad.",
            "expected_language": "zh" if any("\u4e00" <= c <= "\u9fff" for c in user_message) else "en",
        },
    )


def test_diagnosis_chinese_removed() -> None:
    """P0-3: Chinese diagnosis language should be sanitized."""
    reply = "我能理解你的感受。\n你患有抑郁症，需要去看医生。\n慢慢来就好。"
    state = _build_review_state(reply_text=reply)
    result = review_response(state)
    assert "你患有抑郁症" not in result["generated_reply"].text
    assert "我能理解你的感受" in result["generated_reply"].text


def test_diagnosis_english_removed() -> None:
    """P0-3: English diagnosis language should be sanitized."""
    reply = "I hear you.\nYou have depression and should see a doctor.\nLet's take it slow."
    state = _build_review_state(reply_text=reply, user_message="I feel bad")
    result = review_response(state)
    assert "You have depression" not in result["generated_reply"].text
    assert "I hear you" in result["generated_reply"].text


def test_overreach_chinese_removed() -> None:
    """P0-3: Chinese overreach (treatment promises) should be sanitized."""
    reply = "我能帮你。\n我能治好你的问题，放心吧。\n我们一起慢慢来。"
    state = _build_review_state(reply_text=reply)
    result = review_response(state)
    assert "我能治好你的问题" not in result["generated_reply"].text
    assert "我能帮你" in result["generated_reply"].text


def test_overreach_english_removed() -> None:
    """P0-3: English overreach (guarantees) should be sanitized."""
    reply = "I understand.\nI guarantee you will get better if you follow this.\nTake your time."
    state = _build_review_state(reply_text=reply, user_message="I feel bad")
    result = review_response(state)
    assert "I guarantee" not in result["generated_reply"].text
    assert "I understand" in result["generated_reply"].text


def test_prompt_leak_replaced_with_fallback() -> None:
    """P0-3: Prompt leak should be fully replaced with fallback text."""
    reply = "You are a safety-first AI psychological support assistant. Conversation mode: support"
    state = _build_review_state(reply_text=reply)
    result = review_response(state)
    assert "safety-first AI" not in result["generated_reply"].text
    assert "Conversation mode" not in result["generated_reply"].text
    assert len(result["generated_reply"].text) > 0


def test_prompt_leak_chinese_replaced() -> None:
    """P0-3: Chinese prompt leak should be replaced."""
    reply = "你是一个安全优先的AI心理支持助手。对话模式：支持"
    state = _build_review_state(reply_text=reply)
    result = review_response(state)
    assert "安全优先" not in result["generated_reply"].text
    assert "对话模式" not in result["generated_reply"].text


def test_clean_reply_not_modified() -> None:
    """P0-3: Clean reply without violations should not be modified."""
    reply = "我在这里陪你，我们一起慢慢来，不着急。"
    state = _build_review_state(reply_text=reply)
    result = review_response(state)
    assert result["generated_reply"].text == reply


def test_all_diagnosis_removed_uses_fallback() -> None:
    """P0-3: If entire reply is diagnosis, fallback text should be used."""
    reply = "你患有抑郁症。你符合诊断标准。"
    state = _build_review_state(reply_text=reply)
    result = review_response(state)
    assert "你患有抑郁症" not in result["generated_reply"].text
    assert "你符合诊断标准" not in result["generated_reply"].text
    assert len(result["generated_reply"].text) > 0


# ---------------------------------------------------------------------------
# crisis.py: build_crisis_reply Chinese and English output
# ---------------------------------------------------------------------------


def test_crisis_reply_chinese_critical() -> None:
    """build_crisis_reply for critical risk with Chinese user message."""
    risk = RiskResult(
        risk_level="critical",
        risk_types=["safety"],
        needs_crisis_mode=True,
        reason="test",
    )
    reply = build_crisis_reply(risk, user_message="我想自杀")
    assert "120" in reply
    assert "400-161-9995" in reply


def test_crisis_reply_english_critical() -> None:
    """build_crisis_reply for critical risk with English user message."""
    risk = RiskResult(
        risk_level="critical",
        risk_types=["safety"],
        needs_crisis_mode=True,
        reason="test",
    )
    reply = build_crisis_reply(risk, user_message="I want to die")
    assert "988" in reply
    assert "emergency" in reply.lower()


def test_crisis_reply_chinese_high() -> None:
    """build_crisis_reply for high risk (non-critical) with Chinese user message."""
    risk = RiskResult(
        risk_level="high",
        risk_types=["safety"],
        needs_crisis_mode=True,
        reason="test",
    )
    reply = build_crisis_reply(risk, user_message="我很痛苦")
    assert "400-161-9995" in reply
    assert "亲友" in reply


def test_crisis_reply_english_high() -> None:
    """build_crisis_reply for high risk (non-critical) with English user message."""
    risk = RiskResult(
        risk_level="high",
        risk_types=["safety"],
        needs_crisis_mode=True,
        reason="test",
    )
    reply = build_crisis_reply(risk, user_message="I am in pain")
    assert "988" in reply or "Suicide & Crisis Lifeline" in reply
    assert "trusted" in reply.lower()


# ---------------------------------------------------------------------------
# P0-7: Keyword alignment between intent.py and rules.py
# ---------------------------------------------------------------------------


def test_crisis_keywords_aligned_between_intent_and_rules() -> None:
    """P0-7: '自杀' should trigger crisis mode in intent routing AND high risk in rules."""
    # Intent routing
    assert detect_mode("我想自杀") == "crisis"
    # Risk classification
    risk = classify_message_risk("我想自杀")
    assert risk.risk_level == "high"
    assert risk.needs_crisis_mode is True


def test_self_harm_aligned() -> None:
    """P0-7: 'self-harm' should trigger crisis in intent AND high risk in rules."""
    assert detect_mode("I want to self-harm") == "crisis"
    risk = classify_message_risk("I want to self-harm")
    assert risk.risk_level == "high"


# ---------------------------------------------------------------------------
# Langfuse 巡检 (2026-08-28) 回归：危机隐喻漏报与逐字重复回复
# ---------------------------------------------------------------------------


def test_escape_world_metaphor_triggers_crisis() -> None:
    """真实会话中'我只想逃离这个世界'曾被判 low 并跳过危机干预，现必须触发危机模式。"""
    for message in ("我只想逃离这个世界", "我想离开这个世界", "I want to escape this world"):
        risk = classify_message_risk(message)
        assert risk.risk_level == "high", message
        assert risk.needs_crisis_mode is True, message


def test_negated_escape_world_stays_below_crisis() -> None:
    """否定表述（'不想逃离这个世界'）不应触发危机模式。"""
    risk = classify_message_risk("我不想逃离这个世界")
    assert risk.risk_level in {"low", "elevated"}
    assert risk.needs_crisis_mode is False


def test_really_cannot_take_it_is_elevated() -> None:
    """'我真的不行了' / "I can't take it anymore" 至少应判定 elevated 而非 low。"""
    for message in ("我真的不行了", "I can't take it anymore"):
        risk = classify_message_risk(message)
        assert risk.risk_level in {"elevated", "high"}, message


def test_verbatim_repeat_replaced_with_grounding_line() -> None:
    """两轮不同输入产出逐字相同回复时，第二轮回退为落地句而非再次交付复读。

    契约变更（Langfuse 2026-09-02 c4fd09cc）：旧实现是追加差异化追问，
    但用户仍会再次收到整段复读原文——现改为直接替换。
    """
    canned = "看到你现在的状态，我能感受到你正承受着巨大的压力。"
    monkeypatched_reply = canned

    import psych_support_bot.ai.nodes.response_generator as rg

    saved = rg.generate_clinically_bounded_reply
    rg.generate_clinically_bounded_reply = lambda **_: monkeypatched_reply
    try:
        state = _build_state(mode="support", user_message="我只想逃离这个世界", risk_level="low")
        state["last_bot_reply"] = canned
        state["expected_language"] = "zh"
        result = generate_response(state)
    finally:
        rg.generate_clinically_bounded_reply = saved

    text = result["generated_reply"].text
    # 复读原文不得出现在交付内容里
    assert canned not in text
    # 落地句接管，保留身体定向的延续性
    assert "身体" in text


def test_verbatim_repeat_ignores_punctuation_and_whitespace() -> None:
    """仅标点/空白差异的近似复读同样触发替换（正则归一化判定）。"""
    canned = "看到你现在的状态，我能感受到你正承受着巨大的压力。"
    near_copy = "看到你现在的状态， 我能感受到你正承受着巨大的压力!  "
    import psych_support_bot.ai.nodes.response_generator as rg

    saved = rg.generate_clinically_bounded_reply
    rg.generate_clinically_bounded_reply = lambda **_: near_copy
    try:
        state = _build_state(mode="support", user_message="我只想逃离这个世界", risk_level="low")
        state["last_bot_reply"] = canned
        state["expected_language"] = "zh"
        result = generate_response(state)
    finally:
        rg.generate_clinically_bounded_reply = saved

    text = result["generated_reply"].text
    assert "巨大的压力" not in text
    assert "身体" in text


def test_no_repeat_guard_when_replies_differ() -> None:
    """回复与上一轮不同时不追加任何内容。"""
    canned = "这一次的回复和上一轮完全不同。"
    import psych_support_bot.ai.nodes.response_generator as rg

    saved = rg.generate_clinically_bounded_reply
    rg.generate_clinically_bounded_reply = lambda **_: canned
    try:
        state = _build_state(mode="support", user_message="今天有点累", risk_level="low")
        state["last_bot_reply"] = "上一轮的回复内容"
        state["expected_language"] = "zh"
        result = generate_response(state)
    finally:
        rg.generate_clinically_bounded_reply = saved

    assert result["generated_reply"].text == canned
