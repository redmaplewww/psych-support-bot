"""Tests for B1: Focus/concentration keyword routing to procrastination topic."""

from psych_support_bot.ai.knowledge.index import detect_topics


def test_zong_zou_shen_returns_procrastination() -> None:
    """'总是走神' should return procrastination."""
    topics = detect_topics("总是走神")
    assert "procrastination" in topics


def test_attention_not_focused_returns_procrastination() -> None:
    """'注意力不集中' should return procrastination."""
    topics = detect_topics("注意力不集中")
    assert "procrastination" in topics


def test_cannot_focus_returns_procrastination() -> None:
    """'专注不了' should return procrastination."""
    topics = detect_topics("专注不了")
    assert "procrastination" in topics


def test_cannot_concentrate_returns_procrastination() -> None:
    """'无法集中' should return procrastination."""
    topics = detect_topics("无法集中")
    assert "procrastination" in topics


def test_no_motivation_returns_motivation() -> None:
    """'没动力' should return motivation (not procrastination)."""
    topics = detect_topics("没动力")
    assert "motivation" in topics


def test_english_distracted_returns_procrastination() -> None:
    """English 'distracted' should return procrastination."""
    topics = detect_topics("I keep getting distracted")
    assert "procrastination" in topics


def test_english_cant_focus_returns_procrastination() -> None:
    """English 'can't focus' should return procrastination."""
    topics = detect_topics("I can't focus on anything")
    assert "procrastination" in topics
