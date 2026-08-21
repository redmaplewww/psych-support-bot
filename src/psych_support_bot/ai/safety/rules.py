from psych_support_bot.ai.schemas.messages import RiskResult
from psych_support_bot.ai.utils.text_matching import (
    _match_any,
    _normalize_text,
)

HIGH_RISK_KEYWORDS = [
    "suicide",
    "kill myself",
    "self-harm",
    "self harm",
    "hurt myself",
    "hurt someone",
    "want to die",
    "end my life",
    "jump off",
    "overdose",
    "cutting",
    "自杀",
    "想死",
    "不想活了",
    "不想活",
    "活不下去",
    "轻生",
    "寻死",
    "结束生命",
    "结束自己",
    "了结自己",
    "自残",
    "伤害自己",
    "割腕",
    "跳楼",
    "上吊",
    "吞药",
    "服药过量",
]

NEGATION_PATTERNS = [
    "don't want to",
    "dont want to",
    "do not want to",
    "not suicidal",
    "never wanted to die",
    "never thought about",
    "don't have thoughts",
    "no desire to",
    "i am not suicidal",
    "not having suicidal",
    "not thinking about suicide",
]

ELEVATED_RISK_KEYWORDS = [
    "hopeless",
    "panic attack",
    "not sleeping",
    "worthless",
    "stopped sleeping",
    "better off dead",
    "better off",
    "撑不住了",
    "扛不住了",
    "快崩溃了",
    "绝望",
    "没有希望",
    "没意义",
    "没有意义",
    "没有盼头",
    "睡不着",
    "失眠",
    "惊恐发作",
    "喘不过气",
    "活不起",
    "活腻了",
    "想不开",
    "解脱",
    "没救了",
    "死了一了百了",
    "不如死了",
    "死了会更好",
    "死了更好",
    "不配活着",
    "太累了",
    "撑不下去",
]

CRITICAL_RISK_KEYWORDS = [
    "tonight",
    "right now",
    "already took pills",
    "have a knife",
    "have a rope",
    "rope",
    "goodbye forever",
    "今晚",
    "现在就",
    "立刻",
    "马上",
    "等下就",
    "刚刚已经",
    "已经吞药",
    "已经吃了很多药",
    "已经割了",
    "有刀",
    "有绳子",
    "在楼顶",
    "在天台",
    "在桥上",
    "留遗书",
    "最后一条消息",
]

DIRECT_CRITICAL_PATTERNS = [
    "already took pills",
    "已经吞药",
    "已经吃了很多药",
    "已经割了",
    "留遗书",
    "最后一条消息",
    "遗言",
    "遗书",
    "遗言已经写好",
]

MANIA_KEYWORDS = [
    "feel unstoppable",
    "spending all my money",
    "i do not need sleep",
    "four days",
    "racing thoughts",
    "flight of ideas",
    "grandiosity",
    "pressured speech",
    "increased energy",
    "excessive risky behavior",
    "have not slept for days",
    "几天没睡",
    "三天没睡",
    "四天没睡",
    "不用睡觉",
    "精力无限",
    "停不下来",
    "花很多钱",
    "乱花钱",
    "思维飞跃",
    "自我感觉特别好",
    "说话停不下来",
    "觉得自己无所不能",
]

PSYCHOSIS_KEYWORDS = [
    "people are watching me",
    "someone is controlling me",
    "hearing voices",
    "seeing things",
    "听到声音",
    "有人跟我说话",
    "有人控制我",
    "有人监视我",
    "看到别人看不到的东西",
]


CHINESE_SUICIDE_DENIAL_PATTERNS = [
    "不想死",
    "不要死",
    "不会死",
    "没想过死",
    "没想过自杀",
    "没有想死",
    "不想自杀",
]

# B4.2: Negation proximity window.
# A negation only counts if it appears within this many characters
# of a high-risk keyword. This prevents false negatives where the
# negation appears far from the risk word (e.g., "自杀...（500字）...我不想").
NEGATION_WINDOW_CHARS = 20

# Chinese negation words that, when appearing close to a high-risk keyword,
# indicate the user is denying or distancing from the risk.
CHINESE_NEGATION_WORDS = [
    "不",
    "没",
    "没有",
    "不再",
    "不想",
    "没想过",
    "没有想",
]

# English negation words/phrases for proximity checking.
ENGLISH_NEGATION_WORDS = [
    "not",
    "never",
    "no longer",
    "don't",
    "dont",
    "doesn't",
    "without",
    "no desire",
]


def _find_keyword_positions(text: str, keyword: str) -> list[int]:
    """Find all starting positions of keyword in text (case-insensitive)."""
    positions: list[int] = []
    start = 0
    lower_text = text.lower()
    lower_kw = keyword.lower()
    while True:
        idx = lower_text.find(lower_kw, start)
        if idx < 0:
            break
        positions.append(idx)
        start = idx + len(lower_kw)
    return positions


def _has_negation_near_risk(text: str, risk_keywords: list[str]) -> bool:
    """Check if any negation word appears within NEGATION_WINDOW_CHARS of a risk keyword.

    This implements the window-distance detection: the negation must be
    close to the risk word to be a valid denial.
    """
    lower_text = text.lower()

    for kw in risk_keywords:
        kw_lower = kw.lower()
        kw_positions = _find_keyword_positions(lower_text, kw_lower)
        if not kw_positions:
            continue

        for kw_pos in kw_positions:
            # Check the window around this keyword occurrence
            window_start = max(0, kw_pos - NEGATION_WINDOW_CHARS)
            window_end = min(len(lower_text), kw_pos + len(kw_lower) + NEGATION_WINDOW_CHARS)
            window = lower_text[window_start:window_end]

            # Check if any negation word appears in this window
            for neg in CHINESE_NEGATION_WORDS + ENGLISH_NEGATION_WORDS:
                if neg.lower() in window:
                    return True

    return False


def _has_negation(text: str) -> bool:
    """Check for negation patterns.

    B4.2: Enhanced with proximity window detection.
    A negation is only valid if it appears close to a high-risk keyword.
    Falls back to pattern matching for explicit denial phrases.
    """
    normalized, compact = _normalize_text(text)

    # Check explicit denial patterns first (these are self-contained phrases)
    if _match_any(normalized, compact, NEGATION_PATTERNS) or _match_any(
        normalized, compact, CHINESE_SUICIDE_DENIAL_PATTERNS
    ):
        return True

    # B4.2: Check negation proximity to high-risk keywords
    return _has_negation_near_risk(normalized, HIGH_RISK_KEYWORDS)


IMMINENT_MEANS_PATTERNS = [
    "have a rope",
    "have a knife",
    "have pills",
    "有刀",
    "有绳子",
    "有药",
]


def classify_message_risk(text: str) -> RiskResult:
    normalized, compact = _normalize_text(text)
    has_high_risk = _match_any(normalized, compact, HIGH_RISK_KEYWORDS)
    has_critical = _match_any(normalized, compact, CRITICAL_RISK_KEYWORDS)
    has_direct_critical = _match_any(normalized, compact, DIRECT_CRITICAL_PATTERNS)
    has_imminent_means = _match_any(normalized, compact, IMMINENT_MEANS_PATTERNS)
    has_negation = _has_negation(text)

    if has_direct_critical or has_imminent_means or (has_high_risk and has_critical and not has_negation):
        return RiskResult(
            risk_level="critical",
            risk_types=["safety", "immediate_danger"],
            needs_crisis_mode=True,
            reason="Critical self-harm or suicide timing/method language detected.",
        )
    if _match_any(normalized, compact, PSYCHOSIS_KEYWORDS):
        return RiskResult(
            risk_level="high",
            risk_types=["psychosis"],
            needs_crisis_mode=True,
            reason="Possible psychosis-related language detected.",
        )
    if _match_any(normalized, compact, MANIA_KEYWORDS):
        return RiskResult(
            risk_level="high",
            risk_types=["mania"],
            needs_crisis_mode=True,
            reason="Possible mania-related language detected.",
        )
    if has_high_risk:
        if has_negation:
            return RiskResult(
                risk_level="elevated",
                risk_types=["distress"],
                needs_crisis_mode=False,
                reason="Possible self-reassurance or negated safety language detected.",
            )
        return RiskResult(
            risk_level="high",
            risk_types=["safety"],
            needs_crisis_mode=True,
            reason="High-risk safety language detected.",
        )
    if _match_any(normalized, compact, ELEVATED_RISK_KEYWORDS):
        return RiskResult(
            risk_level="elevated",
            risk_types=["distress"],
            needs_crisis_mode=False,
            reason="Elevated distress language detected.",
        )
    return RiskResult(
        risk_level="low",
        risk_types=[],
        needs_crisis_mode=False,
        reason="No obvious high-risk language detected.",
    )
