from psych_support_bot.ai.schemas.messages import ConversationMode
from psych_support_bot.ai.utils.text_matching import (
    _contains_keyword,
    _normalize_text,
)

HELP_KEYWORDS = [
    "how are you",
    "what can you do",
    "who are you",
    "hello",
    "hi",
    "hey",
    "how do you work",
    "怎么用",
    "你能做什么",
    "你是谁",
    "你好",
    "嗨",
]
REFUSAL_KEYWORDS = [
    "skip",
    "pass",
    "don't want",
    "不想做了",
    "不想做",
    "跳过",
    "算了",
    "不要",
    "跳过这题",
]
# Crisis keywords for intent routing.
# Aligned with safety/rules.py: HIGH_RISK_KEYWORDS are used for risk grading,
# CRISIS_KEYWORDS here are used for intent routing (mode=crisis).
# "help me" was removed: too broad, caused false crisis routing on normal help-seeking.
# Both lists must stay in sync for overlapping terms (suicide, self-harm, etc.)
CRISIS_KEYWORDS = [
    "suicide",
    "kill myself",
    "end my life",
    "want to die",
    "self-harm",
    "emergency",
    "crisis",
    "救命",
    "紧急帮助",
    "危机干预",
    "有人吗救救我",
]
PLANNING_KEYWORDS = [
    "plan",
    "next step",
    "schedule",
    "计划",
    "下一步",
    "安排",
    "怎么做",
    "怎么开始",
    "行动计划",
]
INTERVENTION_KEYWORDS = [
    "exercise",
    "breath",
    "breathing exercise",
    "thought record",
    "relaxation",
    "calming exercise",
    "grounding",
    "cbt",
    "act",
    "dbt",
    "panic attack",
    "panic",
    "练习",
    "呼吸",
    "呼吸练习",
    "呼吸法",
    "带我做",
    "教我一个方法",
    "缓解焦虑",
    "放松",
    "想法记录",
    "认知行为",
    "技巧",
    "惊恐发作",
]
ASSESSMENT_KEYWORDS = [
    "assessment",
    "screen",
    "score",
    "评估",
    "测评",
    "量表",
    "测试",
    "筛查",
    "问卷",
    "打分",
    "phq",
    "gad",
]


DIAGNOSIS_KEYWORDS = [
    "diagnose",
    "diagnosis",
    "do i have",
    "am i depressed",
    "am i bipolar",
    "am i autistic",
    "do i have adhd",
    "is it depression",
    "what do i have",
    "what's wrong with me",
    "am i sick",
    "am i crazy",
    "am i mentally ill",
    "do i have a disorder",
    "am i personality disorder",
    "am i schizophrenic",
    "am i ocd",
    "do i have ocd",
    "我是不是抑郁",
    "我是不是抑郁症",
    "我是不是焦虑",
    "我是不是焦虑症",
    "我是不是有病",
    "我有没有病",
    "我是不是双相",
    "我是不是躁郁",
    "我是不是自闭",
    "我是不是多动",
    "我是不是强迫",
    "我是不是人格障碍",
    "我是不是精神分裂",
    "我是什么病",
    "诊断",
    "确诊",
    "我得了什么",
    "我有没有",
    "我是不是有心理问题",
    "我是不是有心理障碍",
    "帮我看一下我是不是",
    "帮我判断我是不是",
    "帮我分析一下我是不是",
]


def detect_mode(text: str) -> ConversationMode:
    normalized, compact = _normalize_text(text)
    stripped = normalized.strip()

    if stripped in {"hello", "hi", "hey", "你好", "嗨"}:
        return "support"
    if any(_contains_keyword(normalized, compact, keyword) for keyword in REFUSAL_KEYWORDS):
        return "support"
    if any(_contains_keyword(normalized, compact, keyword) for keyword in DIAGNOSIS_KEYWORDS):
        return "support"
    if any(_contains_keyword(normalized, compact, keyword) for keyword in CRISIS_KEYWORDS):
        return "crisis"
    if any(_contains_keyword(normalized, compact, keyword) for keyword in INTERVENTION_KEYWORDS):
        return "intervention"
    if any(_contains_keyword(normalized, compact, keyword) for keyword in PLANNING_KEYWORDS):
        return "planning"
    if any(_contains_keyword(normalized, compact, keyword) for keyword in ASSESSMENT_KEYWORDS):
        return "assessment"
    if any(_contains_keyword(normalized, compact, keyword) for keyword in HELP_KEYWORDS):
        return "support"
    return "support"
