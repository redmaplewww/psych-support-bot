"""Tests for B4.3: Exhaustion subtype detection in interview.py."""

from psych_support_bot.ai.interview import (
    determine_interview_process,
)

# --- Exhaustion subtype differentiation ---


def test_physical_exhaustion_gives_physical_hint() -> None:
    """'身体累' should trigger a physical-focused loop_hint."""
    result = determine_interview_process(
        user_message="我最近身体累，睡不够",
        mode="support",
        risk_level="low",
    )
    assert result["interview_stage"] == "exploration"
    assert "physical" in result["loop_hint"].lower()
    assert "sleep" in result["loop_hint"].lower() or "rest" in result["loop_hint"].lower()


def test_emotional_exhaustion_gives_emotional_hint() -> None:
    """'心累' should trigger an emotional-focused loop_hint."""
    result = determine_interview_process(
        user_message="我最近心累，内耗严重",
        mode="support",
        risk_level="low",
    )
    assert result["interview_stage"] == "exploration"
    assert "emotional" in result["loop_hint"].lower()


def test_relational_exhaustion_gives_relational_hint() -> None:
    """'社交疲劳' should trigger a relational-focused loop_hint."""
    result = determine_interview_process(
        user_message="我社交疲劳，不想见人",
        mode="support",
        risk_level="low",
    )
    assert result["interview_stage"] == "exploration"
    assert "relational" in result["loop_hint"].lower() or "social" in result["loop_hint"].lower()


def test_generic_exhaustion_keeps_default_hint() -> None:
    """Generic '累' without subtype should keep the default hint."""
    result = determine_interview_process(
        user_message="我最近很累",
        mode="support",
        risk_level="low",
    )
    assert result["interview_stage"] == "exploration"
    # Default hint asks to clarify the type (contains "physical" as an option)
    assert "emotional" in result["loop_hint"].lower()
    assert "relational" in result["loop_hint"].lower()
    # But should NOT give a specific physical-focused hint
    assert "sleep patterns" not in result["loop_hint"].lower()


def test_english_physical_exhaustion() -> None:
    """English physical exhaustion should give physical hint."""
    result = determine_interview_process(
        user_message="I'm physically exhausted and can't sleep",
        mode="support",
        risk_level="low",
    )
    assert "physical" in result["loop_hint"].lower()


def test_english_emotional_exhaustion() -> None:
    """English emotional exhaustion should give emotional hint."""
    result = determine_interview_process(
        user_message="I feel emotionally drained and overwhelmed",
        mode="support",
        risk_level="low",
    )
    assert "emotional" in result["loop_hint"].lower()


def test_exhaustion_with_contradiction_overrides_subtype() -> None:
    """When exhaustion co-occurs with contradiction, contradiction takes priority."""
    result = determine_interview_process(
        user_message="我身体累但是又不累了",
        mode="support",
        risk_level="low",
    )
    # Contradiction should override the exhaustion subtype hint
    assert result["interview_stage"] == "hypothesis_testing"


def test_exhaustion_with_absolutist_overrides_subtype() -> None:
    """When exhaustion co-occurs with absolutist, hypothesis_testing takes priority."""
    result = determine_interview_process(
        user_message="我永远都心累，根本不可能好",
        mode="support",
        risk_level="low",
    )
    assert result["interview_stage"] == "hypothesis_testing"


def test_crisis_mode_overrides_exhaustion() -> None:
    """Crisis mode should override exhaustion subtype detection."""
    result = determine_interview_process(
        user_message="我身体累",
        mode="crisis",
        risk_level="high",
    )
    assert result["interview_stage"] == "safety_stabilization"
    assert "physical" not in result["loop_hint"].lower()


def test_physical_and_emotional_both_present() -> None:
    """When both physical and emotional are present, physical takes priority (first check)."""
    result = determine_interview_process(
        user_message="我身体累，心也累",
        mode="support",
        risk_level="low",
    )
    assert result["interview_stage"] == "exploration"
    # Physical is checked first
    assert "physical" in result["loop_hint"].lower()
