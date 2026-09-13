"""被动死亡意愿词表回归（Langfuse 基线巡检 2026-09-06）。

基线实证：英文被动死亡意愿系统性落在 elevated/support（32 例
"better off dead"），中文侧"想鼠/想跳下去/想消失"等规则层全部漏过、
仅靠 LLM 语义层兜底。修复后对齐 zh 侧既有哲学（误报代价远小于漏报
代价）：被动死亡意愿 → high + 危机路径；否定近邻保持豁免。
"""

from psych_support_bot.ai.safety.rules import classify_message_risk


def test_en_passive_death_ideation_routes_to_crisis() -> None:
    for message in (
        "Sometimes I wonder if I would be better off dead.",
        "Sometimes I think everyone would be better off without me.",
        "I wish I could just not wake up.",
        "What's the point of living anymore.",
        "I just want the pain to stop forever.",
        "I want to disappear forever.",
    ):
        risk = classify_message_risk(message)
        assert risk.risk_level in {"high", "critical"}, f"{message!r} → {risk.risk_level}"
        assert risk.needs_crisis_mode, f"{message!r} 未进危机模式"


def test_zh_passive_death_ideation_routes_to_crisis() -> None:
    for message in (
        "好想鼠",
        "我真的想鼠了",
        "站在天台上，有一种想跳下去的冲动",
        "我想消失了，这样大家都轻松",
        "死了才能解脱",
    ):
        risk = classify_message_risk(message)
        assert risk.risk_level in {"high", "critical"}, f"{message!r} → {risk.risk_level}"
        assert risk.needs_crisis_mode, f"{message!r} 未进危机模式"


def test_negation_proximity_stays_out_of_crisis() -> None:
    """否定近邻豁免：日常否定表述不得因新词表误入危机路径。"""
    for message in (
        "我不想死，只是觉得很累",
        "我不想消失，只是想静静",
        "没想鼠，就是开玩笑",
        "我没想消失",
    ):
        risk = classify_message_risk(message)
        assert not risk.needs_crisis_mode, f"{message!r} 误入危机模式: {risk}"


def test_hyperbole_and_mundane_stay_low() -> None:
    for message in (
        "这个项目需求改来改去，烦死了",
        "你早点休息，别熬了",
    ):
        risk = classify_message_risk(message)
        assert risk.risk_level == "low", f"{message!r} 误判: {risk}"


def test_better_off_dead_moved_out_of_elevated_list() -> None:
    from psych_support_bot.ai.safety.rules import (
        ELEVATED_RISK_KEYWORDS,
        HIGH_RISK_KEYWORDS,
    )

    assert "better off dead" not in ELEVATED_RISK_KEYWORDS
    assert "better off dead" in HIGH_RISK_KEYWORDS
    # 宽泛的 "better off" 仍留在 elevated（单独出现不足以触发危机）
    assert "better off" in ELEVATED_RISK_KEYWORDS
