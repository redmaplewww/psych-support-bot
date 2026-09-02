from psych_support_bot.ai.schemas.messages import RiskResult

try:
    from psych_support_bot.infra.resources import get_valid_hotlines
except ImportError:
    # Fallback during early development: load the library if it exists.
    import sys
    from pathlib import Path

    repo_root = Path(__file__).parents[3]
    resources_path = repo_root / "src" / "psych_support_bot" / "infra" / "resources"
    if (resources_path / "__init__.py").exists():
        sys.path.insert(0, str(resources_path.parent))
        from psych_support_bot.infra.resources import get_valid_hotlines
    else:
        # Development mode: graceful fallback.
        def get_valid_hotlines(language: str = "zh", *, top_n: int = 3):
            return [
                {
                    "name": "全国心理援助热线",
                    "description": "全国性的心理援助热线(24小时)。",
                    "phone": "400-161-9995",
                }
            ]


def _is_chinese(text: str) -> bool:
    return any("\u4e00" <= c <= "\u9fff" for c in text)


def _resource_description(is_zh: bool, top_n: int = 3) -> str:
    """Return a concise string listing validated resource names + descriptions + phone."""
    items = get_valid_hotlines(language="zh" if is_zh else "en", top_n=top_n)
    if not items:
        return ""
    if is_zh:
        return "。".join(
            f"{item['name']}（{item['description']}，电话：{item['phone']}）" for item in items
        )
    rendered = [
        f"{item['name']} ({item['description']}, phone: {item['phone']})" for item in items
    ]
    if len(rendered) == 1:
        return rendered[0]
    return ", ".join(rendered[:-1]) + ", and " + rendered[-1]


def build_crisis_reply(
    risk: RiskResult,
    *,
    user_message: str = "",
    expected_language: str = "",
) -> str:
    """Generate a crisis-mode reply that prioritizes immediate safety and avoids hallucinated resources.

    Key requirements (safety‑critical):
      1. Always confirm whether the user has already taken action or is about to start.
      2. Provide only general, well‑known resource names + descriptions; never inject
         unverified phone numbers or service hours unless they are listed in the validated
         resource library.
      3. Keep the message short and directive when the risk level is critical.
    """
    is_zh = (expected_language == "zh") if expected_language else (bool(user_message) and _is_chinese(user_message))

    # Safety confirmation question – at least one explicit check.
    zh_confirm = "你现在有没有已经采取行动，或者正准备开始？"
    en_confirm = "Have you already taken action, or are you about to start?"

    # Fetch validated resources for the reply language only.
    resources = _resource_description(is_zh, top_n=3)
    zh_resources = en_resources = resources

    # Critical level: emergency services first, then resources, then confirmation.
    if risk.risk_level == "critical":
        if is_zh:
            return (
                "我很担心你现在的安全。建议你立即拨打120急救，或前往最近的医院急诊，"
                "同时联系一位信任的亲友陪伴你。"
                f"{zh_confirm} {zh_resources}"
            )
        return (
            "I am very concerned that you may be in immediate danger. "
            "Please contact emergency services, go to the nearest hospital now, "
            "and call a trusted person to stay with you. "
            f"{en_confirm} {en_resources}"
        )
    # High risk: emphasize connection + resources, then confirmation.
    if is_zh:
        return (
            "我很担心你的安全。请联系一位信任的亲友陪伴你，拨打120或前往最近的医院，"
            f"{zh_confirm} {zh_resources}"
        )
    return (
        "I am concerned about your safety. Please contact a trusted person, "
        f"go to the nearest hospital, or call emergency services. {en_confirm} {en_resources}"
    )
