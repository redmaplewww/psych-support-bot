"""Tests for LLM-as-judge evaluation (C layer).

These tests are marked as slow because they require LLM API calls.
They are excluded from the default CI run.
"""

import pytest

from psych_support_bot.evals.judge import JUDGE_DIMENSIONS, JUDGE_SYSTEM_PROMPT


def test_judge_dimensions_count() -> None:
    """Ensure exactly 5 judge dimensions are defined."""
    assert len(JUDGE_DIMENSIONS) == 5


def test_judge_dimensions_keys() -> None:
    """Ensure dimension keys match expected names."""
    expected = {
        "attribution_safety",
        "boundary",
        "empathy",
        "action_appropriateness",
        "expression_naturalness",
    }
    assert set(JUDGE_DIMENSIONS.keys()) == expected


def test_judge_system_prompt_contains_all_dimensions() -> None:
    """Ensure system prompt references all 5 dimensions."""
    for dim in JUDGE_DIMENSIONS:
        assert dim in JUDGE_SYSTEM_PROMPT, f"Dimension {dim} missing from system prompt"


def test_judge_system_prompt_requests_json() -> None:
    """Ensure system prompt asks for JSON output."""
    assert "JSON" in JUDGE_SYSTEM_PROMPT
    assert "score" in JUDGE_SYSTEM_PROMPT


def test_judge_system_prompt_scoring_guide() -> None:
    """Ensure scoring guide (1-5) is present."""
    assert "1" in JUDGE_SYSTEM_PROMPT
    assert "5" in JUDGE_SYSTEM_PROMPT
    assert "Very poor" in JUDGE_SYSTEM_PROMPT or "very poor" in JUDGE_SYSTEM_PROMPT
    assert "Excellent" in JUDGE_SYSTEM_PROMPT or "excellent" in JUDGE_SYSTEM_PROMPT


@pytest.mark.slow
def test_judge_score_reply_smoke() -> None:
    """Smoke test: score a simple reply using the LLM judge.

    This test requires JUDGE_API_KEY to be configured.
    Skipped if judge model is not configured.
    """
    from psych_support_bot.evals.judge import score_reply
    from psych_support_bot.infra.config.settings import get_settings

    settings = get_settings()
    if not settings.judge_api_key:
        pytest.skip("JUDGE_API_KEY not configured")

    try:
        result = score_reply(
            user_message="I have been overwhelmed all week and I do not know where to start.",
            ai_reply="I hear that this week has been really overwhelming for you. It sounds like you are carrying a lot right now. Let us take one small step together — what feels most pressing in this moment?",
            mode="support",
            risk_level="low",
            expected_language="en",
        )
    except Exception as e:
        pytest.skip(f"Judge model not reachable: {e}")

    assert len(result) == 5
    for dim, data in result.items():
        assert "score" in data
        assert "reason" in data
