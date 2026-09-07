"""Tests for B5: Structured fallback in build_knowledge_block_prompt.

（Phase 5 后 B5 兜底从 build_context_prompt 迁移到知识区块构建器，
用例随语义迁移，保留 B5 编号以便事故追溯。）
"""

from psych_support_bot.ai.prompts.templates import build_knowledge_block_prompt


def test_empty_knowledge_uses_structured_fallback() -> None:
    """When knowledge_context is empty, fallback should be structured."""
    result = build_knowledge_block_prompt("")
    assert "structured framework" in result.lower()
    assert "reflective listening" in result.lower()
    assert "normalize" in result.lower()
    assert "micro-skill" in result.lower()
    assert "safety check" in result.lower()


def test_provided_knowledge_overrides_fallback() -> None:
    """When knowledge_context is provided, it should be used instead of fallback."""
    result = build_knowledge_block_prompt("CBT anxiety guide: cognitive restructuring")
    assert "CBT anxiety guide" in result
    assert "structured framework" not in result.lower()


def test_fallback_mentions_evidence_based_approaches() -> None:
    """Fallback should mention CBT, ACT, DBT, MI."""
    result = build_knowledge_block_prompt("")
    assert "CBT" in result
    assert "ACT" in result
    assert "DBT" in result
    assert "MI" in result
