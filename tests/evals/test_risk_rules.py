from psych_support_bot.ai.safety.rules import classify_message_risk


def test_high_risk_message_detected() -> None:
    result = classify_message_risk("I want to kill myself soon and feel hopeless")
    assert result.risk_level == "high"
    assert result.needs_crisis_mode is True


def test_elevated_message_detected() -> None:
    result = classify_message_risk("I feel hopeless and cannot go on")
    assert result.risk_level == "elevated"
    assert result.needs_crisis_mode is False


def test_high_risk_chinese_message_detected() -> None:
    result = classify_message_risk("我不想活了，我想伤害自己")
    assert result.risk_level == "high"
    assert result.needs_crisis_mode is True


def test_elevated_chinese_distress_detected() -> None:
    result = classify_message_risk("我快撑不住了，最近一直失眠，感觉没有意义")
    assert result.risk_level == "elevated"
    assert result.needs_crisis_mode is False


def test_elevated_chinese_huobeqi_detected() -> None:
    result = classify_message_risk("活着太累了，活不起")
    assert result.risk_level == "elevated"
    assert result.needs_crisis_mode is False


def test_elevated_chinese_xiangbukai_detected() -> None:
    result = classify_message_risk("我真的想不开，不知道还能撑多久")
    assert result.risk_level == "elevated"
    assert result.needs_crisis_mode is False


def test_elevated_chinese_jiatuo_detected() -> None:
    result = classify_message_risk("希望这一切能早点解脱")
    assert result.risk_level == "elevated"
    assert result.needs_crisis_mode is False


def test_negated_high_risk_downgrades_to_elevated() -> None:
    result = classify_message_risk("I do not want to die anymore")
    assert result.risk_level == "elevated"
    assert result.needs_crisis_mode is False


def test_negated_high_risk_chinese_downgrades_to_elevated() -> None:
    result = classify_message_risk("我不想死了")
    assert result.risk_level == "elevated"
    assert result.needs_crisis_mode is False


def test_negated_selfharm_downgrades_to_elevated() -> None:
    result = classify_message_risk("I do not want to hurt myself anymore")
    assert result.risk_level == "elevated"
    assert result.needs_crisis_mode is False


def test_bare_better_off_phrase_is_elevated() -> None:
    result = classify_message_risk("I'd be better off taking the bus instead")
    assert result.risk_level == "elevated"
    assert result.needs_crisis_mode is False


def test_not_suicidal_is_low() -> None:
    result = classify_message_risk("I am not suicidal")
    assert result.risk_level == "low"
    assert result.needs_crisis_mode is False


def test_mania_grandiosity_detected() -> None:
    result = classify_message_risk("I have been feeling like I can accomplish anything and have not slept for days")
    assert result.risk_level == "high"
    assert "mania" in result.risk_types
    assert result.needs_crisis_mode is True


def test_psychosis_voices_detected() -> None:
    result = classify_message_risk("I have been hearing voices that others cannot hear")
    assert result.risk_level == "high"
    assert "psychosis" in result.risk_types
    assert result.needs_crisis_mode is True
