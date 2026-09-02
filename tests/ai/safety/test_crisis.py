"""Unit tests for crisis reply generation (safety‑critical).

Design note: hotline numbers are rendered ONLY from the validated resource
library (`psych_support_bot.infra.resources`). The templates themselves must
never hardcode numbers — so tests assert that every number in the reply comes
from the library, and that unverified numbers never appear.
"""

import re

from psych_support_bot.ai.safety.crisis import build_crisis_reply
from psych_support_bot.ai.schemas.messages import RiskResult
from psych_support_bot.infra.resources import get_valid_hotlines


def _build_risk(level: str = "critical", types: list[str] | None = None) -> RiskResult:
    return RiskResult(
        risk_level=level,
        risk_types=types or [f"{level}_risk"],
        demand_intervention=True,
        reason="Test risk",
    )


def _library_phones() -> set[str]:
    """Collect every phone number the validated library would ever expose."""
    phones: set[str] = set()
    for lang in ("zh", "en"):
        for item in get_valid_hotlines(language=lang, top_n=10):
            phones.add(item["phone"])
    return phones


def test_critical_chinese_contains_confirmation() -> None:
    """Critical risk reply must ask whether the user has taken action."""
    reply = build_crisis_reply(
        risk=_build_risk("critical", ["safety", "immediate_danger"]),
        user_message="我已经决定今晚结束自己的生命，也已经想好具体怎么做了。",
        expected_language="zh",
    )
    assert "采取行动" in reply or "准备开始" in reply
    # 紧急服务建议存在
    assert "120" in reply
    # 必须包含资源库中的资源
    assert "全国心理援助热线" in reply
    assert "400-161-9995" in reply


def test_critical_english_contains_confirmation() -> None:
    """Critical risk reply must ask whether the user has taken action."""
    reply = build_crisis_reply(
        risk=_build_risk("critical", ["safety", "immediate_danger"]),
        user_message="I've decided to end it tonight and I have a plan.",
        expected_language="en",
    )
    assert "taken action" in reply or "about to start" in reply
    assert "emergency services" in reply
    # 资源来自资源库（英文语言 -> 988 Lifeline）
    assert "988" in reply
    assert "Suicide & Crisis Lifeline" in reply


def test_high_risk_must_also_confirm() -> None:
    """Even high risk requires a check; crisis resources may be mentioned."""
    reply = build_crisis_reply(
        risk=_build_risk("high"),
        user_message="最近一直在想这件事，觉得自己撑不住了。",
        expected_language="zh",
    )
    assert "采取行动" in reply
    # 不要求 120/急诊，但必须要有亲友联系或医院建议
    assert ("信任的亲友" in reply) or ("医院" in reply)


def test_all_phones_verified_from_library() -> None:
    """Every phone-like number in a reply must come from the validated library
    (or be a public emergency number allowed in the template)."""
    replies = [
        build_crisis_reply(
            risk=_build_risk("critical", ["safety", "immediate_danger"]),
            expected_language="zh",
        ),
        build_crisis_reply(risk=_build_risk("high"), expected_language="en"),
    ]
    allowed = _library_phones() | {"120", "110", "119", "911"}
    phone_re = re.compile(r"\d[\d-]{2,}\d")
    for reply in replies:
        for raw in phone_re.findall(reply):
            assert raw in allowed, f"unverified phone number in reply: {raw}"

def test_critical_includes_emergency_services() -> None:
    """Critical must explicitly mention 120/emergency services."""
    zh = build_crisis_reply(
        risk=_build_risk("critical", ["safety", "immediate_danger"]),
        expected_language="zh",
    )
    en = build_crisis_reply(
        risk=_build_risk("critical", ["safety", "immediate_danger"]),
        expected_language="en",
    )
    assert "120" in zh
    assert "emergency services" in en


def test_high_risk_prioritizes_trusted_person() -> None:
    """High risk should emphasize trusted contact before generic resources."""
    zh = build_crisis_reply(
        risk=_build_risk("high"),
        user_message="I can't take it anymore.",
        expected_language="zh",
    )
    # 亲友联系应该在资源之前
    friends_index = zh.find("亲友")
    helpline_index = zh.find("热线")
    if helpline_index != -1:
        assert 0 < friends_index < helpline_index
    # 强烈建议医院或急救
    assert "医院" in zh or "120" in zh


def test_resources_include_names_and_descriptions() -> None:
    """Replies include resource name + description + library-verified phone."""
    zh = build_crisis_reply(
        risk=_build_risk("critical", ["safety", "immediate_danger"]),
        user_message="我撑不住了。",
        expected_language="zh",
    )
    en = build_crisis_reply(
        risk=_build_risk("high"),
        user_message="I can't take it anymore.",
        expected_language="en",
    )
    assert "热线" in zh
    assert "Lifeline" in en
    zh_items = get_valid_hotlines(language="zh", top_n=3)
    for item in zh_items:
        assert item["name"] in zh
        assert item["phone"] in zh
    en_items = get_valid_hotlines(language="en", top_n=3)
    for item in en_items:
        assert item["name"] in en
        assert item["phone"] in en
