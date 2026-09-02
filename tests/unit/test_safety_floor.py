"""Safety floor: a recent flagged screening must raise the risk floor."""

from psych_support_bot.ai.nodes.risk_classifier import classify_risk


def _state(message: str = "今天就是有点累", floor: str = ""):
    return {
        "user_message": message,
        "memory_summary": "",
        "mode": "support",
        "risk_result": None,  # replaced by the node itself
        "safety_floor_risk_level": floor,
    }


def test_floor_elevated_upgrades_low() -> None:
    state = _state(floor="elevated")
    result = classify_risk(state)["risk_result"]
    assert result.risk_level == "elevated"
    assert "recent_screening_flag" in result.risk_types
    assert result.needs_crisis_mode is False


def test_floor_high_arms_crisis_mode() -> None:
    state = _state(floor="high")
    out = classify_risk(state)
    assert out["risk_result"].risk_level == "high"
    assert out["risk_result"].needs_crisis_mode is True
    assert out["mode"] == "crisis"


def test_no_floor_leaves_detection_alone() -> None:
    state = _state(floor="")
    result = classify_risk(state)["risk_result"]
    assert result.risk_level == "low"


def test_native_high_survives_lower_floor() -> None:
    # Direct declaration classifies HIGH natively; an elevated floor must
    # never pull it back down.
    state = _state(message="我不想活了", floor="elevated")
    result = classify_risk(state)["risk_result"]
    assert result.risk_level == "high"
    assert result.needs_crisis_mode is True


def test_low_turn_lifted_to_elevated_floor() -> None:
    # The actual regression from production traces: ambiguous turn while a
    # flagged PHQ-9 is recent stayed LOW because no keywords matched.
    state = _state(
        message="别人一说话我就心烦，忍不住摇头",
        floor="elevated",
    )
    result = classify_risk(state)["risk_result"]
    assert result.risk_level == "elevated"
    assert "recent_screening_flag" in result.risk_types
