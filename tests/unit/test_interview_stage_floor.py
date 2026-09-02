"""Stage-floor escalation: keyword cascade keeps priority, depth breaks ties."""

from psych_support_bot.ai.interview import determine_interview_process


def _call(msg: str, turn_count: int):
    return determine_interview_process(
        user_message=msg, mode="support", risk_level="low", turn_count=turn_count
    )


def test_early_turns_stay_engagement() -> None:
    r = _call("嗯", 0)
    assert r["interview_stage"] == "engagement"
    assert r["question_strategy"] == "open"


def test_mid_conversation_escalates_to_exploration() -> None:
    r = _call("嗯", 3)
    assert r["interview_stage"] == "exploration"
    assert "previous turns" in r["loop_hint"]


def test_long_conversation_escalates_to_pattern_analysis() -> None:
    r = _call("嗯", 8)
    assert r["interview_stage"] == "pattern_analysis"
    assert r["question_strategy"] == "looping"


def test_keyword_cascade_still_beats_depth_floor() -> None:
    # 一切/绝对 statements hit ABSOLUTIST keywords → hypothesis_testing
    r = _call("我永远都做不好，一切都没有希望", 10)
    assert r["interview_stage"] == "hypothesis_testing"
    assert r["challenge_allowed"] is True


def test_crisis_overrides_depth_floor() -> None:
    r = determine_interview_process(
        user_message="嗯", mode="crisis", risk_level="high", turn_count=9
    )
    assert r["interview_stage"] == "safety_stabilization"
