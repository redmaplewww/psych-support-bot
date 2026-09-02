"""Tests for B5: Structured fallback in build_context_prompt."""

from psych_support_bot.ai.prompts.templates import build_context_prompt


def test_empty_knowledge_uses_structured_fallback() -> None:
    """When knowledge_context is empty, fallback should be structured."""
    result = build_context_prompt("user memory", "")
    assert "structured framework" in result.lower()
    assert "reflective listening" in result.lower()
    assert "normalize" in result.lower()
    assert "micro-skill" in result.lower()
    assert "safety check" in result.lower()


def test_none_knowledge_uses_structured_fallback() -> None:
    """When knowledge_context is None (via empty string), fallback should be structured."""
    result = build_context_prompt("", "")
    assert "structured framework" in result.lower()
    assert "No prior memory" in result


def test_provided_knowledge_overrides_fallback() -> None:
    """When knowledge_context is provided, it should be used instead of fallback."""
    result = build_context_prompt("memory", "CBT anxiety guide: cognitive restructuring")
    assert "CBT anxiety guide" in result
    assert "structured framework" not in result.lower()


def test_fallback_mentions_evidence_based_approaches() -> None:
    """Fallback should mention CBT, ACT, DBT, MI."""
    result = build_context_prompt("", "")
    assert "CBT" in result
    assert "ACT" in result
    assert "DBT" in result
    assert "MI" in result
