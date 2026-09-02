"""Validated safety-related resources (hotlines, clinics, etc.).

This module must NOT be imported by the core application before configuration
is loaded – otherwise a missing `resources` package would fail the whole import path.
The crisis module imports it dynamically with a fallback for development.
"""

# Hotline / safety-related resources (validated sources).

# This module provides a single function `get_valid_hotlines` that returns
# the name + description of up to three top-priority resources for the
# given language.  The crisis template then uses generic descriptions
# instead of concrete phone numbers, avoiding hallucinated or obsolete
# information.
#
# To add a new entry, create a dictionary with the fields below and append
# it to `_HOTLINES`.  Priority 1-3 controls which entries surface first when
# only a short list is needed.  Sources must be official / auditable.
#
# NOTE: The crisis output must only render phone numbers returned by
# `get_valid_hotlines` — never hardcode numbers in reply templates.

_HOTLINES = [
    {
        "name": "全国心理援助热线",
        "type": "national",
        "language": "zh",
        "region": "CN",
        "phone": "400-161-9995",
        "hours": "24小时",
        "description": "全国性的心理援助热线，由协会运营并提供专业心理支持。",
        "source": "https://mp.weixin.qq.com/s/...",
        "priority": 1,
        "active": True,
    },
    {
        "name": "988 Suicide & Crisis Lifeline (US)",
        "type": "national",
        "language": "en",
        "region": "US",
        "phone": "988",
        "hours": "24/7",
        "description": "美国国家自杀与危机救助热线(拨打或发短信 988)。",
        "source": "https://988lifeline.org",
        "priority": 1,
        "active": True,
    },
    {
        "name": "Crisis Text Line (US)",
        "type": "national",
        "language": "en",
        "region": "US",
        "phone": "741741",
        "description": "美国危机短信求助热线(text 发送至 741741)。",
        "source": "https://textline.app",
        "priority": 2,
        "active": True,
    },
    {
        "name": "北京心理危机研究与干预中心",
        "type": "regional",
        "language": "zh",
        "region": "CN-Beijing",
        "phone": "010-82951332",
        "hours": "周一至周五 9:00-17:00",
        "description": "北京市心理危机研究与干预中心, 提供专业的危机干预服务。",
        "source": "https://www.bjpc.org.cn",
        "priority": 3,
        "active": True,
    },
]


def get_valid_hotlines(language: str = "zh", *, top_n: int = 3) -> list[dict]:
    """Return up to *top_n* validated hotline entries for *language*.

    Returns a list of dictionaries with keys:
        - name
        - description
        - phone
        (hours, region are kept for future use but should not
          be directly rendered in the crisis reply.)

    The result is sorted by priority and language.
    """
    lang = "zh" if language in {"zh", "cn"} else "en"
    filtered = [
        h
        for h in _HOTLINES
        if h["language"] == lang and h["active"]
    ]
    # Sort by priority, then by name for determinism.
    filtered.sort(key=lambda x: (x["priority"], x["name"]))
    return [
        {
            "name": h["name"],
            "description": h["description"],
            "phone": h["phone"],
        }
        for h in filtered[:top_n]
    ]
