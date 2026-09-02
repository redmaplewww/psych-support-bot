import re

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
    # 被动死亡意愿的隐喻表述。Langfuse 巡检发现真实会话中
    # "我只想逃离这个世界" 被判 low，漏掉了危机干预。
    # 误报代价（多展示一次危机资源）远小于漏报代价，故纳入高危。
    "逃离这个世界",
    "离开这个世界",
    "想去另一个世界",
    "escape this world",
    "leave this world",
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
    # Langfuse 巡检补充：真实会话中"我真的不行了"被判 low。
    # 痛苦升级信号，但语义不足以触发危机模式，纳入 elevated。
    "熬不住了",
    "真的不行了",
    "快不行了",
    "can't take it anymore",
    "cannot take it anymore",
]

# C2: Regex patterns for Chinese elevated-risk synonym variants.
# These cover expressions that pure substring matching in ELEVATED_RISK_KEYWORDS
# would miss, such as 没意思 (vs 没意义), 没用, 没价值, 没动力, etc.
# Each pattern is designed to match the core semantic concept without
# false-positiving on unrelated uses (e.g. 不是这个意思 → NOT a match).
ELEVATED_RISK_REGEX_PATTERNS_ZH: list[str] = [
    # Worthlessness / meaninglessness: 没/无 + 意思/意义/价值/用/劲
    r"没(有什么)?(意思|意义|价值|用|劲)",
    r"无(意思|意义|价值|动力)",
    r"没什么(意思|意义|价值|用|干劲|动力)",
    r"活着没(意思|意义|价值|用)",
    r"觉得没(意思|意义|价值|用)",
    # Hopelessness variants
    r"没(有什么)?希望",
    r"没(有什么)?盼头",
    r"看不到希望",
    r"没(有什么)?动力",
    r"没(有什么)?干劲",
    # Exhaustion variants beyond 太累了
    r"累(得)?不行",
    r"累(到)?不想动",
    r"身心俱疲",
    r"精疲力竭",
]

_ELEVATED_RISK_REGEX_ZH = [re.compile(p, re.IGNORECASE) for p in ELEVATED_RISK_REGEX_PATTERNS_ZH]

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

# Chinese negation words that, when appearing BEFORE a high-risk keyword,
# indicate the user is denying or distancing from the risk.
#
# NOTE: deliberately excludes the bare characters 不 / 没 — they occur inside
# the risk expressions themselves ("不想活了"), which turned every direct
# declaration into a false "denial" via self-substring matching. Genuine
# denials use compound forms already listed here (没想过 / 没有想 / 不再…).
CHINESE_NEGATION_WORDS = [
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
    """Check whether a risk keyword occurrence is locally denied.

    Denials hug the phrase on either side:
    - LEFT (prefix ends with the negation): “没想过死”, “don't … hurt”
    - RIGHT (suffix starts with it, optional 但/现在/but filler):
      “…自杀但现在不想了”, “…suicide but not anymore”

    A bare 不 inside the expression itself (“不想活”) is part of the
    declaration, never a denial — hence no substring scanning.
    """
    lower_text = text.lower()
    all_negation_words = [w.lower() for w in CHINESE_NEGATION_WORDS + ENGLISH_NEGATION_WORDS]

    def _denied_left(prefix: str) -> bool:
        cleaned = prefix.rstrip("，。！？、,.!? ")
        return any(cleaned.endswith(neg) for neg in all_negation_words)

    def _denied_right(suffix: str) -> bool:
        head = suffix.lstrip("，。！？、,.!? ")[:24]
        if not head:
            return False
        if any(head.startswith(neg) for neg in CHINESE_NEGATION_WORDS):
            return True
        return bool(
            re.match(r"^(?:但是|可是|而是|然后|现在)?\s*[^，。！？]{0,3}?[不没]", head)
            or re.search(r"^(?:but\s+)?(?:not\s+anymore|no longer|not)\b", head)
        )

    for kw in risk_keywords:
        kw_lower = kw.lower()
        kw_positions = _find_keyword_positions(lower_text, kw_lower)
        if not kw_positions:
            continue

        for kw_pos in kw_positions:
            window_start = max(0, kw_pos - NEGATION_WINDOW_CHARS)
            prefix = lower_text[window_start:kw_pos]
            suffix_start = min(len(lower_text), kw_pos + len(kw_lower))
            suffix = lower_text[suffix_start : suffix_start + NEGATION_WINDOW_CHARS]

            if _denied_left(prefix) or _denied_right(suffix):
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
    if _match_any(normalized, compact, ELEVATED_RISK_KEYWORDS) or any(
        p.search(normalized) for p in _ELEVATED_RISK_REGEX_ZH
    ):
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
