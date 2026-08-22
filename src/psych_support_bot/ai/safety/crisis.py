from psych_support_bot.ai.schemas.messages import RiskResult


def _is_chinese(text: str) -> bool:
    return any("\u4e00" <= c <= "\u9fff" for c in text)


def build_crisis_reply(risk: RiskResult, *, user_message: str = "", expected_language: str = "") -> str:
    if expected_language:
        is_zh = expected_language == "zh"
    else:
        is_zh = bool(user_message) and _is_chinese(user_message)
    if risk.risk_level == "critical":
        if is_zh:
            return (
                "我很担心你现在的安全。建议你立即拨打120急救，或前往最近的医院急诊，"
                "同时联系一位信任的亲友陪伴你。如果你已经采取了行动，请告诉身边人或医护人员。\n\n"
                "全国心理援助热线：400-161-9995（24小时）\n"
                "北京心理危机研究与干预中心：010-82951332"
            )
        return (
            "I am very concerned that you may be in immediate danger. "
            "Please contact emergency services, go to the nearest hospital now, "
            "and call a trusted person to stay with you. "
            "If you have already taken action, please tell those around you what happened.\n\n"
            "988 Suicide & Crisis Lifeline (US): Call or text 988\n"
            "Crisis Text Line: Text HOME to 741741\n"
            "International: https://findahelpline.com"
        )
    if is_zh:
        return (
            "我很担心你的安全。请联系一位信任的亲友陪伴你，拨打120或前往最近的医院，"
            "也可以拨打心理援助热线。\n\n"
            "全国心理援助热线：400-161-9995（24小时）\n"
            "北京心理危机研究与干预中心：010-82951332\n"
            "如果暂时联系不到人，请先试着告诉身边的人你的感受。"
        )
    return (
        "I am concerned about your safety. Please contact a trusted person, "
        "go to the nearest hospital, or call emergency services. You do not have to face this alone.\n\n"
        "988 Suicide & Crisis Lifeline (US): Call or text 988\n"
        "Crisis Text Line: Text HOME to 741741\n"
        "International: https://findahelpline.com"
    )
