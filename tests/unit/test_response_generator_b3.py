"""Tests for B3.3: Refusal context injection in response_generator."""

from typing import cast

from psych_support_bot.ai.nodes.response_generator import (
    _inject_refusal_context,
    generate_response,
)
from psych_support_bot.ai.schemas.messages import (
    GeneratedReply,
    RiskResult,
)
from psych_support_bot.ai.schemas.state import GraphState


def _build_state(
    *,
    refusal_history: list[str] | None = None,
    loop_hint: str = "Start broad.",
    mode: str = "support",
) -> GraphState:
    return cast(
        GraphState,
        {
            "user_id": "test-user",
            "session_id": "session-1",
            "user_message": "我最近很焦虑",
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
            "topics": [],
            "fallback_used": False,
            "consultation_required": False,
            "consultation_agents": [],
            "consultation_notes": "",
            "consultation_opinions": [],
            "interview_stage": "engagement",
            "question_strategy": "open",
            "challenge_allowed": False,
            "loop_hint": loop_hint,
            "exercise_history": [],
            "refusal_history": refusal_history if refusal_history is not None else [],
            "expected_language": "zh" if any("\u4e00" <= c <= "\u9fff" for c in "我最近很焦虑") else "en",
        },
    )


def test_refusal_history_injected_into_loop_hint() -> None:
    """When refusal_history is non-empty, loop_hint should contain refusal note."""
    state = _build_state(refusal_history=["anxiety", "rumination"])
    _inject_refusal_context(state)
    assert "declined" in state["loop_hint"].lower()
    assert "anxiety" in state["loop_hint"]
    assert "rumination" in state["loop_hint"]


def test_empty_refusal_history_does_not_modify_hint() -> None:
    """When refusal_history is empty, loop_hint should be unchanged."""
    state = _build_state(refusal_history=[], loop_hint="Start broad.")
    _inject_refusal_context(state)
    assert state["loop_hint"] == "Start broad."


def test_refusal_note_prepended_to_existing_hint() -> None:
    """Refusal note should be prepended, existing hint preserved."""
    state = _build_state(refusal_history=["stress"], loop_hint="Explore the pattern.")
    _inject_refusal_context(state)
    assert "Explore the pattern." in state["loop_hint"]
    assert state["loop_hint"].startswith("User has previously declined")


def test_generate_response_with_refusal_history_injects_context(monkeypatch) -> None:
    """Full generate_response should inject refusal context before LLM call."""
    captured_hint = {}

    def _capture_hint(**kwargs):
        captured_hint["loop_hint"] = kwargs.get("loop_hint", "")
        return "supportive reply"

    monkeypatch.setattr(
        "psych_support_bot.ai.nodes.response_generator.generate_clinically_bounded_reply",
        _capture_hint,
    )
    state = _build_state(refusal_history=["anxiety"])
    generate_response(state)
    assert "declined" in captured_hint["loop_hint"].lower()
    assert "anxiety" in captured_hint["loop_hint"]


def test_generate_response_without_refusal_history_no_injection(monkeypatch) -> None:
    """No refusal history should not inject anything."""
    captured_hint = {}

    def _capture_hint(**kwargs):
        captured_hint["loop_hint"] = kwargs.get("loop_hint", "")
        return "supportive reply"

    monkeypatch.setattr(
        "psych_support_bot.ai.nodes.response_generator.generate_clinically_bounded_reply",
        _capture_hint,
    )
    state = _build_state(refusal_history=[])
    generate_response(state)
    assert "declined" not in captured_hint["loop_hint"].lower()
