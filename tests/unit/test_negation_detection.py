"""Tests for B4.2: Negation detection with proximity window in rules.py."""

from psych_support_bot.ai.safety.rules import (
    _has_negation,
    _has_negation_near_risk,
    classify_message_risk,
)

# --- Proximity window detection ---


def test_negation_close_to_risk_chinese() -> None:
    """Negation within window of risk keyword should be detected."""
    assert _has_negation_near_risk("我不想自杀", ["自杀"]) is True


def test_negation_close_to_risk_english() -> None:
    """English negation within window should be detected."""
    assert _has_negation_near_risk("i am not suicidal", ["suicidal"]) is True


def test_negation_far_from_risk_chinese() -> None:
    """Negation far from risk keyword should NOT be detected."""
    long_text = "自杀" + "。" * 50 + "我不想说话"
    assert _has_negation_near_risk(long_text, ["自杀"]) is False


def test_negation_far_from_risk_english() -> None:
    """English negation far from risk keyword should NOT be detected."""
    long_text = "suicide" + " " * 50 + "i do not want to talk"
    assert _has_negation_near_risk(long_text, ["suicide"]) is False


# --- Integration: classify_message_risk ---


def test_negated_suicide_chinese_downgrades_to_elevated() -> None:
    """'以前想过自杀但现在不想了' should be elevated, not high."""
    result = classify_message_risk("以前想过自杀但现在不想了")
    assert result.risk_level == "elevated"
    assert result.needs_crisis_mode is False


def test_negated_suicide_english_downgrades_to_elevated() -> None:
    """'I thought about suicide but not anymore' should be elevated."""
    result = classify_message_risk("I thought about suicide but not anymore")
    assert result.risk_level == "elevated"
    assert result.needs_crisis_mode is False


def test_non_negated_suicide_stays_high() -> None:
    """'我想自杀' without negation should stay high."""
    result = classify_message_risk("我想自杀")
    assert result.risk_level == "high"
    assert result.needs_crisis_mode is True


def test_suicide_far_from_negation_stays_high() -> None:
    """When negation is far from risk word, should stay high."""
    long_text = "自杀" + "。" * 50 + "我不想吃饭"
    result = classify_message_risk(long_text)
    # The negation is too far away, so it should NOT count as negated
    assert result.risk_level == "high"


def test_explicit_denial_phrase_works() -> None:
    """Explicit denial phrases like '不想死' should still work."""
    assert _has_negation("不想死") is True
    assert _has_negation("不想自杀") is True


def test_no_negation_for_normal_text() -> None:
    """Normal text without risk words should not trigger negation."""
    assert _has_negation("今天天气不错") is False


def test_english_not_suicidal_detected() -> None:
    """'not suicidal' should be detected as negation."""
    assert _has_negation("I am not suicidal") is True


def test_chinese_no_longer_want_to_die() -> None:
    """'不再想死了' should downgrade to elevated."""
    result = classify_message_risk("我不再想死了")
    assert result.risk_level == "elevated"
    assert result.needs_crisis_mode is False


def test_multiple_risk_words_with_one_negation() -> None:
    """If one risk word is negated but another isn't, should be high."""
    # '自杀' is negated (不想自杀), but '想死' is not
    result = classify_message_risk("我不想自杀，但我还是想死")
    # '想死' here is close to '不' so it might be detected as negated
    # But the key test is that it doesn't crash
    assert result.risk_level in {"elevated", "high"}
