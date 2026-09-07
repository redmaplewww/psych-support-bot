"""规则层 mania 裸词误触发回归（Langfuse 2026-09-06 流量模拟实证）。

裸词「停不下来」曾把"项目一直加班，感觉停不下来"这类日常抱怨确定性
误判为 high + mania + needs_crisis_mode=True，直接误入危机路径。
修复后：裸词移除，躁狂判定由"说话停不下来"等特异短语承载。
"""

from psych_support_bot.ai.safety.rules import classify_message_risk


def test_overtime_colloquial_not_mania() -> None:
    """加班抱怨里的"停不下来"不得触发 mania/crisis 路由。"""
    for message in (
        "就是工作上的事，项目一直加班，感觉停不下来",
        "最近剧太好看了一直刷，停不下来",
        "事情太多，感觉忙得停不下来",
    ):
        risk = classify_message_risk(message)
        assert risk.risk_level != "high" or "mania" not in risk.risk_types, f"{message!r} 误判为 mania: {risk}"
        assert not risk.needs_crisis_mode, f"{message!r} 误入危机模式: {risk}"


def test_specific_mania_phrases_still_trigger() -> None:
    """特异躁狂短语（压力性言语/夸大/精力无限）保持命中。"""
    risk = classify_message_risk("我最近觉得自己无所不能，精力无限，说话停不下来")
    assert "mania" in risk.risk_types
    assert risk.risk_level == "high"
    assert risk.needs_crisis_mode is True


def test_mania_keyword_list_has_no_bare_colloquial() -> None:
    """裸词「停不下来」不得单独存在于 MANIA_KEYWORDS。"""
    from psych_support_bot.ai.safety.rules import MANIA_KEYWORDS

    assert "停不下来" not in MANIA_KEYWORDS
    # 特异短语仍在
    assert "说话停不下来" in MANIA_KEYWORDS
