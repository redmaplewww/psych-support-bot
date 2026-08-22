"""Tests for B2.2: Cross-turn risk tracking in risk_classifier."""

from typing import cast

from psych_support_bot.ai.nodes.risk_classifier import (
    _has_previous_elevated,
    classify_risk,
)
from psych_support_bot.ai.schemas.messages import (
    GeneratedReply,
    RiskResult,
)
from psych_support_bot.ai.schemas.state import GraphState


def _build_state(
    *,
    user_message: str = "hello",
    memory_summary: str = "",
) -> GraphState:
    return cast(
        GraphState,
        {
            "user_id": "test-user",
            "session_id": "session-1",
            "user_message": user_message,
            "memory_summary": memory_summary,
            "knowledge_context": "",
            "mode": "support",
            "risk_result": RiskResult(
                risk_level="low",
                risk_types=[],
                needs_crisis_mode=False,
                reason="test",
            ),
            "generated_reply": GeneratedReply(
                text="",
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
            "challenge_allowed": False,
            "loop_hint": "Start broad.",
            "expected_language": "zh" if any("\u4e00" <= c <= "\u9fff" for c in user_message) else "en",
        },
    )


# --- Previous elevated detection ---


def test_prev_elevated_detected_via_risk_marker() -> None:
    assert _has_previous_elevated("User (mode=support, risk=elevated): ...") is True


def test_prev_elevated_detected_via_chinese_keywords() -> None:
    assert _has_previous_elevated("我最近觉得很绝望") is True


def test_prev_elevated_detected_via_english_keywords() -> None:
    assert _has_previous_elevated("I feel hopeless and worthless") is True


def test_prev_elevated_not_detected_with_low_risk() -> None:
    assert _has_previous_elevated("User (mode=support, risk=low): hello") is False


def test_prev_elevated_not_detected_with_empty_memory() -> None:
    assert _has_previous_elevated("") is False


def test_prev_elevated_not_detected_with_neutral_text() -> None:
    assert _has_previous_elevated("今天天气不错，去买东西了") is False


# --- Cross-turn risk upgrade ---


def test_consecutive_elevated_upgrades_to_high() -> None:
    """Current elevated + previous elevated -> upgraded to high."""
    state = _build_state(
        user_message="我还是觉得没意义，撑不住了",
        memory_summary="User (mode=support, risk=elevated): 我觉得很绝望，没有希望",
    )
    result = classify_risk(state)
    assert result["risk_result"].risk_level == "high"
    assert result["risk_result"].needs_crisis_mode is True
    assert "cumulative_elevated" in result["risk_result"].risk_types
    assert result["mode"] == "crisis"


def test_first_elevated_stays_elevated() -> None:
    """First-time elevated with no prior elevated in memory stays elevated."""
    state = _build_state(
        user_message="我觉得很绝望",
        memory_summary="User (mode=support, risk=low): 今天还好",
    )
    result = classify_risk(state)
    assert result["risk_result"].risk_level == "elevated"
    assert result["risk_result"].needs_crisis_mode is False


def test_elevated_with_empty_memory_stays_elevated() -> None:
    """First message in session with elevated keywords stays elevated."""
    state = _build_state(
        user_message="我觉得没有希望",
        memory_summary="",
    )
    result = classify_risk(state)
    assert result["risk_result"].risk_level == "elevated"


def test_low_risk_not_affected_by_previous_elevated() -> None:
    """Low risk current message should not upgrade even if previous was elevated."""
    state = _build_state(
        user_message="今天感觉还行",
        memory_summary="User (mode=support, risk=elevated): 我觉得很绝望",
    )
    result = classify_risk(state)
    assert result["risk_result"].risk_level == "low"


def test_high_risk_not_affected_by_previous_elevated() -> None:
    """High risk current message should stay high (no downgrade logic)."""
    state = _build_state(
        user_message="我想自杀",
        memory_summary="User (mode=support, risk=elevated): 我觉得很绝望",
    )
    result = classify_risk(state)
    assert result["risk_result"].risk_level in {"high", "critical"}


def test_english_consecutive_elevated_upgrades() -> None:
    """English: consecutive elevated should also upgrade."""
    state = _build_state(
        user_message="I still feel hopeless and worthless",
        memory_summary="User (mode=support, risk=elevated): I feel hopeless",
    )
    result = classify_risk(state)
    assert result["risk_result"].risk_level == "high"
    assert "cumulative_elevated" in result["risk_result"].risk_types
