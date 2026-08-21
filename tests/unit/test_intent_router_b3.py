"""Tests for B3.2: Refusal recording in intent_router."""

from typing import cast

from psych_support_bot.ai.nodes.intent_router import route_intent
from psych_support_bot.ai.schemas.messages import (
    GeneratedReply,
    RiskResult,
)
from psych_support_bot.ai.schemas.state import GraphState


def _build_state(
    *,
    mode: str = "intervention",
    user_message: str = "不想做了",
    topics: list[str] | None = None,
    refusal_history: list[str] | None = None,
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
                risk_level="low",
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
            "topics": topics if topics is not None else ["anxiety"],
            "fallback_used": False,
            "consultation_required": False,
            "consultation_agents": [],
            "consultation_notes": "",
            "consultation_opinions": [],
            "interview_stage": "engagement",
            "question_strategy": "open",
            "challenge_allowed": False,
            "loop_hint": "Start broad.",
            "exercise_history": [],
            "refusal_history": refusal_history or [],
        },
    )


def test_refusal_in_intervention_records_topic() -> None:
    """When user refuses in intervention mode, topic is recorded in refusal_history."""
    state = _build_state(
        mode="intervention",
        user_message="不想做了",
        topics=["anxiety", "rumination"],
    )
    result = route_intent(state)
    assert "anxiety" in result["refusal_history"]
    assert "rumination" in result["refusal_history"]


def test_refusal_in_support_does_not_record() -> None:
    """Refusal in support mode should not record in refusal_history."""
    state = _build_state(
        mode="support",
        user_message="不想做了",
        topics=["anxiety"],
    )
    result = route_intent(state)
    assert result["refusal_history"] == []


def test_refusal_in_crisis_skipped() -> None:
    """Crisis mode should skip routing entirely."""
    state = _build_state(
        mode="crisis",
        user_message="不想做了",
        topics=["anxiety"],
    )
    result = route_intent(state)
    assert result["mode"] == "crisis"
    assert result["refusal_history"] == []


def test_no_refusal_does_not_record() -> None:
    """Non-refusal message should not add to refusal_history."""
    state = _build_state(
        mode="intervention",
        user_message="我想做呼吸练习",
        topics=["anxiety"],
    )
    result = route_intent(state)
    assert result["refusal_history"] == []


def test_english_refusal_records() -> None:
    """English refusal should also record."""
    state = _build_state(
        mode="intervention",
        user_message="skip this exercise",
        topics=["stress"],
    )
    result = route_intent(state)
    assert "stress" in result["refusal_history"]


def test_duplicate_refusal_not_recorded_twice() -> None:
    """If topic already in refusal_history, don't add again."""
    state = _build_state(
        mode="intervention",
        user_message="不想做了",
        topics=["anxiety"],
        refusal_history=["anxiety"],
    )
    result = route_intent(state)
    assert result["refusal_history"].count("anxiety") == 1


def test_refusal_with_no_topics_records_empty() -> None:
    """Refusal with no active topics records nothing."""
    state = _build_state(
        mode="intervention",
        user_message="跳过",
        topics=[],
    )
    result = route_intent(state)
    assert result["refusal_history"] == []
