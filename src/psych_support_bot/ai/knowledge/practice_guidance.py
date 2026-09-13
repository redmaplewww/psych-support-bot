"""分场景实践指导语料（P3-B 迁入：语料数据统一归 ai/knowledge/）。

原 tools/knowledge_base.py 内联数据迁此——所有知识语料与
cbt/dbt/act/sfbt_mi 等模块同层存放；检索门面（tools/knowledge_base）
只做组装与预算控制。
"""

KNOWLEDGE_SNIPPETS = {
    "support": [
        "Validate emotion before offering suggestions.",
        "Use plain-language psychoeducation to explain common stress, anxiety, sleep, or mood reactions.",
        "Offer reassurance and one manageable next step, not a treatment sequence.",
    ],
    "assessment": [
        "Clarify duration, frequency, severity, and impact on daily functioning.",
        "Use non-diagnostic language and simple explanation rather than medicalized framing.",
        "Ask at most one or two focused follow-up questions.",
    ],
    "intervention": [
        "Only offer a skill when the user clearly wants one.",
        "Prefer light grounding, calming, or self-observation over therapy-heavy protocols.",
        "Keep practice steps concrete, brief, and low pressure.",
    ],
    "planning": [
        "Translate insight into a low-friction action for today.",
        "Prefer actions that increase stability, rest, and self-kindness.",
        "Keep plans realistic enough to complete under stress.",
    ],
    "crisis": [
        "Use short, direct safety-oriented language.",
        "Do not explore causes deeply during crisis routing.",
        "Encourage real-world support and urgent care if danger is immediate.",
    ],
}
