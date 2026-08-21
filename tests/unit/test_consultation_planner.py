"""Tests for B2.1: Cross-turn contradiction detection in consultation_planner."""

from typing import cast

from psych_support_bot.ai.nodes.consultation_planner import (
    _detect_cross_turn_contradiction,
    _extract_emotion_directions,
    plan_consultation,
)
from psych_support_bot.ai.schemas.messages import (
    ConversationMode,
    GeneratedReply,
    RiskResult,
)
from psych_support_bot.ai.schemas.state import GraphState


def _build_state(
    *,
    mode: ConversationMode = "support",
    user_message: str = "I feel okay",
    memory_summary: str = "",
    risk_level: str = "low",
) -> GraphState:
    return cast(
        GraphState,
        {
            "user_id": "test-user",
            "session_id": "session-1",
            "user_message": user_message,
            "memory_summary": memory_summary,
            "knowledge_context": "",
            "mode": mode,
            "risk_result": RiskResult(
                risk_level=risk_level,
                risk_types=[],
                needs_crisis_mode=False,
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
        },
    )


# --- Emotion direction extraction ---


def test_negative_state_detected() -> None:
    assert _extract_emotion_directions("我最近很焦虑") == {"negative"}


def test_positive_state_detected() -> None:
    assert _extract_emotion_directions("我好多了") == {"positive"}


def test_neutral_state_returns_empty() -> None:
    assert _extract_emotion_directions("今天天气不错") == set()


def test_english_negative_detected() -> None:
    assert _extract_emotion_directions("I feel very depressed") == {"negative"}


def test_english_positive_detected() -> None:
    assert _extract_emotion_directions("I'm feeling much better now") == {"positive"}


# --- Cross-turn contradiction detection ---


def test_negative_to_positive_shift_detected() -> None:
    memory = "profile || recent summary | 我最近很焦虑，睡不着觉"
    current = "我今天好多了，不焦虑了"
    hint = _detect_cross_turn_contradiction(memory, current)
    assert hint is not None
    assert "improvement" in hint.lower() or "shift" in hint.lower()


def test_positive_to_negative_shift_detected() -> None:
    memory = "profile || summary | 我最近好多了，很开心"
    current = "我今天又很沮丧，很难过"
    hint = _detect_cross_turn_contradiction(memory, current)
    assert hint is not None
    assert "setback" in hint.lower() or "shift" in hint.lower()


def test_no_contradiction_when_same_direction() -> None:
    memory = "我最近很焦虑"
    current = "今天还是一样紧张"
    hint = _detect_cross_turn_contradiction(memory, current)
    assert hint is None


def test_no_contradiction_when_neutral_current() -> None:
    memory = "我最近很焦虑"
    current = "今天去买了点东西"
    hint = _detect_cross_turn_contradiction(memory, current)
    assert hint is None


def test_no_contradiction_with_empty_memory() -> None:
    hint = _detect_cross_turn_contradiction("", "我好多了")
    assert hint is None


def test_english_negative_to_positive_detected() -> None:
    memory = "profile || User (mode=support): I feel so depressed and hopeless."
    current = "I'm feeling much better today, actually good."
    hint = _detect_cross_turn_contradiction(memory, current)
    assert hint is not None
    assert "improvement" in hint.lower() or "shift" in hint.lower()


# --- Integration: plan_consultation with contradiction ---


def test_plan_consultation_injects_contradiction_hint() -> None:
    state = _build_state(
        user_message="我好多了，完全不焦虑了",
        memory_summary="我最近很焦虑，睡不着觉",
    )
    result = plan_consultation(state)
    assert "contradiction" in result["loop_hint"].lower()


def test_plan_consultation_no_contradiction_without_memory() -> None:
    state = _build_state(
        user_message="我很焦虑",
        memory_summary="",
    )
    result = plan_consultation(state)
    assert "contradiction" not in result["loop_hint"].lower()


def test_plan_consultation_keeps_original_hint_when_no_contradiction() -> None:
    state = _build_state(
        user_message="今天还是一样焦虑",
        memory_summary="我最近很焦虑",
    )
    result = plan_consultation(state)
    # Should not contain contradiction prefix
    assert "contradiction" not in result["loop_hint"].lower()
    # Should contain the original interview process hint
    assert len(result["loop_hint"]) > 0
