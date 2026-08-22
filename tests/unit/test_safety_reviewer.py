"""Tests for B2.3: Challenge review in safety_reviewer."""

from typing import cast

from psych_support_bot.ai.nodes.safety_reviewer import (
    _detect_challenge,
    _sanitize_challenge,
    review_response,
)
from psych_support_bot.ai.schemas.messages import (
    GeneratedReply,
    RiskResult,
)
from psych_support_bot.ai.schemas.state import GraphState


def _build_state(
    *,
    reply_text: str,
    challenge_allowed: bool = False,
    user_message: str = "我最近很难受",
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
            "mode": "support",
            "risk_result": RiskResult(
                risk_level=risk_level,
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


# --- Challenge detection ---


def test_detect_chinese_challenge() -> None:
    assert _detect_challenge("你确定你真的没事吗？") is True


def test_detect_english_challenge() -> None:
    assert _detect_challenge("Are you sure about that?") is True


def test_detect_chinese_why_question() -> None:
    assert _detect_challenge("你为什么不试试说出来呢？") is True


def test_detect_english_have_you_considered() -> None:
    assert _detect_challenge("Have you considered talking to someone?") is True


def test_no_challenge_in_supportive_text() -> None:
    assert _detect_challenge("我在这里陪你，我们一起慢慢来。") is False


def test_no_challenge_in_normal_english() -> None:
    assert _detect_challenge("I hear you, that sounds really difficult.") is False


# --- Sanitize challenge ---


def test_sanitize_removes_challenge_keeps_rest() -> None:
    text = "我听到你了。\n你确定你真的没事吗？\n我们一起想想办法。"
    sanitized, was_modified = _sanitize_challenge(text)
    assert was_modified is True
    assert "你确定" not in sanitized
    assert "我听到你了" in sanitized
    assert "我们一起想想办法" in sanitized


def test_sanitize_all_challenge_returns_empty() -> None:
    text = "你确定吗？\n你为什么不试试？"
    sanitized, was_modified = _sanitize_challenge(text)
    assert was_modified is True
    assert sanitized == ""


# --- Integration: review_response ---


def test_challenge_removed_when_not_allowed() -> None:
    """When challenge_allowed=False, challenge sentences should be removed."""
    reply = "我能理解你的感受。\n你确定你真的没事吗？\n慢慢来就好。"
    state = _build_state(reply_text=reply, challenge_allowed=False)
    result = review_response(state)
    assert "你确定" not in result["generated_reply"].text
    assert "我能理解你的感受" in result["generated_reply"].text


def test_challenge_kept_when_allowed() -> None:
    """When challenge_allowed=True, challenge sentences should be kept."""
    reply = "我听到你说的了。\n你确定你真的没事吗？\n我们可以再聊聊。"
    state = _build_state(reply_text=reply, challenge_allowed=True)
    result = review_response(state)
    assert "你确定" in result["generated_reply"].text


def test_all_challenge_replaced_with_fallback() -> None:
    """If the entire reply is challenge, fallback text should be used."""
    reply = "你确定吗？\n你为什么不改变呢？"
    state = _build_state(reply_text=reply, challenge_allowed=False)
    result = review_response(state)
    # Should be replaced with fallback
    assert "你确定" not in result["generated_reply"].text
    assert "你为什么" not in result["generated_reply"].text
    assert len(result["generated_reply"].text) > 0


def test_english_challenge_removed_when_not_allowed() -> None:
    """English challenge should also be removed."""
    reply = "I hear you.\nAre you sure about that?\nLet's take it slow."
    state = _build_state(reply_text=reply, challenge_allowed=False, user_message="I feel bad")
    result = review_response(state)
    assert "Are you sure" not in result["generated_reply"].text
    assert "I hear you" in result["generated_reply"].text


def test_no_false_positive_on_supportive_reply() -> None:
    """Supportive replies should not be flagged as challenge."""
    reply = "我在这里陪你，你不必着急，我们慢慢来。"
    state = _build_state(reply_text=reply, challenge_allowed=False)
    result = review_response(state)
    assert result["generated_reply"].text == reply


def test_diagnosis_takes_priority_over_challenge() -> None:
    """Diagnosis patterns should be handled first (existing priority)."""
    reply = "你患有抑郁症。\n你确定你没事吗？"
    state = _build_state(reply_text=reply, challenge_allowed=False)
    result = review_response(state)
    assert "你患有抑郁症" not in result["generated_reply"].text
