"""Structured index and retrieval helpers for psychological knowledge."""

from dataclasses import dataclass

from psych_support_bot.ai.knowledge.act import ACT_EXERCISES
from psych_support_bot.ai.knowledge.cbt import CBT_EXERCISES, CBT_INTERVENTION_GUIDES
from psych_support_bot.ai.knowledge.crisis import (
    CRISIS_RISK_LEVELS,
    SAFETY_PLAN_TEMPLATE,
)
from psych_support_bot.ai.knowledge.dbt import DBT_EXERCISES
from psych_support_bot.ai.knowledge.foundations import FOUNDATIONAL_KNOWLEDGE
from psych_support_bot.ai.knowledge.psychoeducation import PSYCHOEDUCATION_MODULES
from psych_support_bot.ai.knowledge.sfbt_mi import MI_TOOLS, SFBT_TOOLS
from psych_support_bot.knowledge_ingestion import (
    knowledge_data_dir,
    load_all_corpora,
    load_learning_notes,
)

TOPIC_KEYWORDS = {
    "anxiety": [
        "anxious",
        "anxiety",
        "worry",
        "worried",
        "fear",
        "nervous",
        "焦虑",
        "焦慮",
        "焦躁",
        "紧张",
        "心慌",
        "担心",
        "总担心",
    ],
    "panic": [
        "panic",
        "panic attack",
        "racing heart",
        "can't breathe",
        "dizzy",
        "惊恐",
        "惊恐发作",
        "恐慌",
        "恐慌发作",
        "心跳很快",
        "喘不过气",
        "胸闷",
        "濒死感",
    ],
    "depression": [
        "depressed",
        "depression",
        "hopeless",
        "empty",
        "low mood",
        "worthless",
        "抑郁",
        "憂鬱",
        "情绪低落",
        "提不起劲",
        "没意思",
        "绝望",
        "没用",
    ],
    "sleep": [
        "sleep",
        "insomnia",
        "can't sleep",
        "awake",
        "bedtime",
        "night",
        "失眠",
        "睡不着",
        "睡不著",
        "入睡困难",
        "早醒",
        "睡眠差",
    ],
    "ocd": [
        "ocd",
        "obsession",
        "compulsion",
        "checking",
        "contamination",
        "intrusive thoughts",
        "强迫",
        "強迫",
        "强迫症",
        "強迫症",
        "反复检查",
        "侵入性想法",
    ],
    "burnout": [
        "burnout",
        "burned out",
        "exhausted",
        "overworked",
        "drained",
        "倦怠",
        "疲惫",
        "累垮了",
        "透支",
    ],
    "grief": [
        "grief",
        "loss",
        "bereaved",
        "funeral",
        "miss them",
        "died",
        "哀伤",
        "丧失",
        "失去",
        "想念他",
        "想念她",
    ],
    "anger": [
        "angry",
        "anger",
        "rage",
        "furious",
        "irritated",
        "愤怒",
        "生气",
        "暴怒",
        "烦躁",
    ],
    "procrastination": [
        "procrastinating",
        "procrastination",
        "avoid",
        "avoiding",
        "stuck",
        "can't start",
        "拖延",
        "逃避",
        "开始不了",
        "卡住了",
        # focus/concentration keywords merged into procrastination (knowledge freeze)
        "走神",
        "分心",
        "无法集中",
        "看不进书",
        "坐不住",
        "concentration",
        "distracted",
        "can't focus",
        "不专注",
        "注意力散",
    ],
    "rumination": [
        "ruminating",
        "rumination",
        "overthinking",
        "looping",
        "can't stop thinking",
        "反刍",
        "反复想",
        "脑子停不下来",
        "胡思乱想",
        "钻牛角尖",
    ],
    "self_worth": [
        "worthless",
        "not good enough",
        "hate myself",
        "self-criticism",
        "shame",
        "我很没用",
        "不够好",
        "讨厌自己",
        "自责",
        "羞耻",
    ],
    "relationships": [
        "relationship",
        "partner",
        "friend",
        "argument",
        "boundary",
        "conflict",
        "关系",
        "伴侣",
        "朋友",
        "吵架",
        "边界",
        "冲突",
        "孤独",
        "孤单",
        "没人理解",
        "没朋友",
        "孤立",
        "被孤立",
        "社交隔离",
    ],
    "stress": [
        "stress",
        "stressed",
        "pressure",
        "overloaded",
        "tense",
        "压力",
        "压得喘不过气",
        "绷着",
        "紧绷",
    ],
    "motivation": [
        "unmotivated",
        "motivation",
        "lazy",
        "freeze",
        "shutdown",
        "没动力",
        "提不起劲",
        "停摆",
        "冻结住了",
        # focus/concentration keywords merged into motivation (knowledge freeze)
        "注意力不集中",
        "专注不了",
        "看不进去",
        "注意力涣散",
        "无法专心",
        "can't concentrate",
        "can't pay attention",
        "mind wandering",
    ],
    "social_anxiety": [
        "social anxiety",
        "judged",
        "embarrassed",
        "people think",
        "public speaking",
        "社交焦虑",
        "社恐",
        "怕别人看我",
        "怕被评价",
        "害怕丢脸",
        "公开讲话",
        "不敢出门",
        "怕见人",
        "社交回避",
        "怕丢脸",
        "怕被笑话",
    ],
    "ptsd": [
        "ptsd",
        "创伤后",
        "创伤",
        "创伤后应激",
        "被袭击",
        "事故后",
        "trauma",
        "traumatized",
        "惊恐反应",
        "闪回",
        "入侵性记忆",
        "噩梦连连",
    ],
    "eating_disorder": [
        "eating disorder",
        "暴食",
        "暴食症",
        "神经性贪食",
        "神经性厌食",
        " anorexia",
        "bulimia",
        "进食障碍",
        "吃太多",
        "控制不住吃",
        "吃完后悔",
        "催吐",
    ],
    "nssi": [
        "self injury",
        "自伤",
        "非自杀性自伤",
        "割伤自己",
        "撞头",
        "self-harm",
        "自我伤害",
        "弄伤自己",
    ],
    "substance_use": [
        "substance",
        "alcohol",
        "drug",
        "喝酒",
        "酗酒",
        "吸毒",
        "药物滥用",
        "依赖",
        "成瘾",
        "戒断",
    ],
}


@dataclass(frozen=True)
class KnowledgeEntry:
    entry_id: str
    title: str
    source: str
    topics: tuple[str, ...]
    modes: tuple[str, ...]
    keywords: tuple[str, ...]
    summary: str
    content: str
    action_hint: str = ""


def _clip(text: str, limit: int = 320) -> str:
    compact = " ".join(text.split())
    if len(compact) <= limit:
        return compact
    return f"{compact[: limit - 3].rstrip()}..."


from psych_support_bot.ai.utils.text_matching import _contains_keyword, _normalize_text


def _normalize_keyword(keyword: str) -> str:
    return _normalize_text(keyword)[1]


def _topic_keywords(topics: tuple[str, ...]) -> tuple[str, ...]:
    keywords: list[str] = []
    for topic in topics:
        keywords.extend(TOPIC_KEYWORDS.get(topic, []))
    keywords.extend(topics)
    return tuple(dict.fromkeys(keyword for keyword in keywords if keyword))


def detect_topics(user_message: str) -> list[str]:
    normalized, compact = _normalize_text(user_message)
    scored: list[tuple[int, str]] = []
    for topic, keywords in TOPIC_KEYWORDS.items():
        score = 0
        for keyword in keywords:
            if _contains_keyword(normalized, compact, keyword):
                score += 2 if len(_normalize_keyword(keyword)) >= 4 else 1
        if score > 0:
            scored.append((score, topic))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [topic for _, topic in scored[:3]]


def _entry(
    entry_id: str,
    title: str,
    source: str,
    topics: tuple[str, ...],
    modes: tuple[str, ...],
    keywords: tuple[str, ...],
    content: str,
    action_hint: str = "",
) -> KnowledgeEntry:
    return KnowledgeEntry(
        entry_id=entry_id,
        title=title,
        source=source,
        topics=topics,
        modes=modes,
        keywords=keywords,
        summary=_clip(content, 220),
        content=content,
        action_hint=action_hint,
    )


def build_knowledge_index() -> list[KnowledgeEntry]:
    entries: list[KnowledgeEntry] = []
    module_topics = {
        "anxiety_overview": ("anxiety",),
        "anxiety_overview_zh": ("anxiety",),
        "depression_overview": ("depression",),
        "depression_overview_zh": ("depression",),
        "insomnia_overview": ("sleep",),
        "insomnia_overview_zh": ("sleep",),
        "panic_attacks_overview": ("panic", "anxiety"),
        "panic_attacks_overview_zh": ("anxiety",),
        "ocd_overview": ("ocd",),
        "burnout_overview": ("burnout", "stress"),
        "normal_vs_clinical_distress": ("stress", "depression", "anxiety"),
    }

    for module_id, module in PSYCHOEDUCATION_MODULES.items():
        topics = module_topics.get(module_id, (str(module.get("category", "general")),))
        keywords = _topic_keywords(topics)
        entries.append(
            _entry(
                entry_id=f"psychoeducation:{module_id}",
                title=str(module["title"]),
                source="psychoeducation",
                topics=topics,
                modes=("support", "assessment", "planning", "intervention"),
                keywords=keywords,
                content=str(module["content"]),
                action_hint="Use to explain symptoms in non-diagnostic language and encourage help-seeking when impairment is significant.",
            )
        )

    for guide_id, guide in CBT_INTERVENTION_GUIDES.items():
        topic = guide_id.removesuffix("_general")
        entries.append(
            _entry(
                entry_id=f"cbt-guide:{guide_id}",
                title=f"CBT Guide: {guide_id.replace('_', ' ').title()}",
                source="cbt_guide",
                topics=(topic,),
                modes=("support", "intervention", "planning"),
                keywords=_topic_keywords((topic,)),
                content=guide,
                action_hint="Choose one concrete CBT-informed next step rather than offering many suggestions at once.",
            )
        )

    exercise_topic_map = {
        "thought_record_full": ("anxiety", "depression", "rumination", "self_worth"),
        "behavioral_activation": (
            "depression",
            "burnout",
            "motivation",
            "procrastination",
        ),
        "mindfulness_body_scan": ("anxiety", "panic", "stress", "sleep"),
        "worry_tree": ("anxiety", "rumination"),
        "downward_arrow": ("anxiety", "self_worth"),
        "values_card_sort": ("motivation", "stress", "burnout"),
        "commitment_obstacle": ("procrastination", "motivation", "burnout"),
        "defusion_labeling": ("rumination", "anxiety", "self_worth"),
        "acceptance_leaves": ("grief", "stress", "rumination"),
        "tipp_full": ("panic", "anger", "stress"),
        "wise_mind": ("relationships", "anger", "stress"),
        "radical_acceptance_walkthrough": ("grief", "stress", "relationships"),
        "dear_man_assertion": ("relationships",),
    }

    for exercise_id, exercise in CBT_EXERCISES.items():
        title = exercise.name
        description = exercise.description
        entries.append(
            _entry(
                entry_id=f"cbt-exercise:{exercise_id}",
                title=title,
                source="cbt_exercise",
                topics=tuple(exercise_topic_map.get(exercise_id, ("stress",))),
                modes=("intervention", "planning"),
                keywords=_topic_keywords(tuple(exercise_topic_map.get(exercise_id, ("stress",)))),
                content=description,
                action_hint=f"Exercise tag: cbt_{exercise_id}",
            )
        )

    for exercise_id, exercise in ACT_EXERCISES.items():
        entries.append(
            _entry(
                entry_id=f"act-exercise:{exercise_id}",
                title=str(exercise["name"]),
                source="act_exercise",
                topics=tuple(exercise_topic_map.get(exercise_id, ("stress",))),
                modes=("intervention", "planning"),
                keywords=_topic_keywords(tuple(exercise_topic_map.get(exercise_id, ("stress",)))),
                content=str(exercise["description"]),
                action_hint=f"Exercise tag: act_{exercise_id}",
            )
        )

    for exercise_id, exercise in DBT_EXERCISES.items():
        entries.append(
            _entry(
                entry_id=f"dbt-exercise:{exercise_id}",
                title=str(exercise["name"]),
                source="dbt_exercise",
                topics=tuple(exercise_topic_map.get(exercise_id, ("stress",))),
                modes=("intervention", "planning", "crisis"),
                keywords=_topic_keywords(tuple(exercise_topic_map.get(exercise_id, ("stress",)))),
                content=str(exercise["description"]),
                action_hint=f"Exercise tag: dbt_{exercise_id}",
            )
        )

    for module_id, module in FOUNDATIONAL_KNOWLEDGE.items():
        category = str(module["category"])
        entries.append(
            _entry(
                entry_id=f"foundation:{module_id}",
                title=str(module["title"]),
                source="foundation",
                topics=(category,),
                modes=("support", "assessment", "planning", "intervention"),
                keywords=_topic_keywords((category,)),
                content=str(module["content"]),
                action_hint="Use as a framing note to validate the pattern before offering a skill.",
            )
        )

    entries.append(
        _entry(
            entry_id="crisis:safety-plan",
            title=str(SAFETY_PLAN_TEMPLATE["name"]),
            source="crisis",
            topics=("crisis",),
            modes=("crisis",),
            keywords=("safety", "plan", "crisis", "support", "安全", "危机", "支持"),
            content=str(SAFETY_PLAN_TEMPLATE["description"]),
            action_hint="Focus on warning signs, coping strategies, support people, and means restriction.",
        )
    )

    for level, data in CRISIS_RISK_LEVELS.items():
        resources = "; ".join(data.get("required_resources", []))
        behaviors = " ".join(data.get("ai_behavior", [])[:3])
        content = str(data["description"])
        if resources:
            content += f" Resources: {resources}"
        if behaviors:
            content += f" Response stance: {behaviors}"
        entries.append(
            _entry(
                entry_id=f"crisis:{level}",
                title=f"Crisis Protocol: {level.title()}",
                source="crisis",
                topics=("crisis",),
                modes=("crisis",),
                keywords=(level, "crisis", "safety", "危机", "安全"),
                content=content,
                action_hint="Use short, direct, safety-first language and prioritize real-world support.",
            )
        )

    entries.append(
        _entry(
            entry_id="sfbt:coping-questions",
            title=str(SFBT_TOOLS["coping_questions"]["name"]),
            source="sfbt",
            topics=("stress", "depression", "burnout"),
            modes=("support",),
            keywords=("cope", "coping", "survive", "getting through", "撑过来", "应对"),
            content=str(SFBT_TOOLS["coping_questions"]["description"]),
            action_hint="Use when the user feels defeated; highlight what is already helping them survive.",
        )
    )

    entries.append(
        _entry(
            entry_id="mi:change-talk",
            title=str(MI_TOOLS["change_talk"]["name"]),
            source="mi",
            topics=("motivation", "procrastination", "burnout"),
            modes=("planning", "support"),
            keywords=(
                "change",
                "motivation",
                "ready",
                "willing",
                "改变",
                "动力",
                "准备好",
            ),
            content=str(MI_TOOLS["change_talk"]["description"]),
            action_hint="Reflect desire, ability, reasons, and commitment language rather than pushing the user.",
        )
    )

    for chunk in load_all_corpora():
        details: list[str] = [f"Source: {chunk.publisher}. URL: {chunk.url}"]
        if chunk.chapter_hint:
            details.append(f"Chapter: {chunk.chapter_hint}")
        if chunk.treatment_modalities:
            details.append("Modalities: " + ", ".join(chunk.treatment_modalities))
        if chunk.audience:
            details.append("Audience: " + ", ".join(chunk.audience))
        entries.append(
            _entry(
                entry_id=chunk.entry_id,
                title=chunk.title,
                source=f"external_{chunk.publisher.lower()}",
                topics=chunk.topics,
                modes=chunk.modes,
                keywords=tuple(dict.fromkeys([*chunk.keywords, *_topic_keywords(chunk.topics)])),
                content=chunk.content,
                action_hint=" | ".join(details),
            )
        )

    for note in load_learning_notes():
        details = [
            f"Synthesized from {note.source_count} sources",
            "Source ids: " + ", ".join(note.source_ids[:6]),
        ]
        if note.treatment_modalities:
            details.append("Modalities: " + ", ".join(note.treatment_modalities))
        if note.audience:
            details.append("Audience: " + ", ".join(note.audience))
        if note.practice_points:
            details.append("Practice points: " + " || ".join(note.practice_points))
        entries.append(
            _entry(
                entry_id=note.note_id,
                title=note.title,
                source="active_learning",
                topics=note.topics,
                modes=note.modes,
                keywords=tuple(dict.fromkeys([*note.keywords, *_topic_keywords(note.topics)])),
                content=note.summary,
                action_hint=" | ".join(details),
            )
        )

    return entries


def _knowledge_index_signature() -> tuple[tuple[str, bool, int, int], ...]:

    data_dir = knowledge_data_dir()
    corpus_files = (
        data_dir / "public_corpus.json",
        data_dir / "local_corpus.json",
        data_dir / "ingested_corpus.json",
        data_dir / "learning_notes.json",
    )
    signature: list[tuple[str, bool, int, int]] = []
    for path in corpus_files:
        if path.exists():
            stats = path.stat()
            signature.append((str(path), True, stats.st_mtime_ns, stats.st_size))
        else:
            signature.append((str(path), False, 0, 0))
    return tuple(signature)


_KNOWLEDGE_INDEX_CACHE: list[KnowledgeEntry] | None = None
_KNOWLEDGE_INDEX_SIGNATURE: tuple[tuple[str, bool, int, int], ...] | None = None


def get_knowledge_index(*, force_reload: bool = False) -> list[KnowledgeEntry]:
    global _KNOWLEDGE_INDEX_CACHE, _KNOWLEDGE_INDEX_SIGNATURE

    if not _KNOWLEDGE_INDEX_CACHE:
        _KNOWLEDGE_INDEX_CACHE = build_knowledge_index()
        _KNOWLEDGE_INDEX_SIGNATURE = _knowledge_index_signature()
        return _KNOWLEDGE_INDEX_CACHE

    signature = _knowledge_index_signature()
    if force_reload or signature != _KNOWLEDGE_INDEX_SIGNATURE:
        _KNOWLEDGE_INDEX_CACHE = build_knowledge_index()
        _KNOWLEDGE_INDEX_SIGNATURE = signature
    return _KNOWLEDGE_INDEX_CACHE


def retrieve_knowledge_entries(
    user_message: str,
    mode: str,
    risk_level: str,
    *,
    limit: int = 4,
) -> list[KnowledgeEntry]:
    topics = detect_topics(user_message)
    normalized, compact = _normalize_text(user_message)
    scored: list[tuple[int, KnowledgeEntry]] = []

    for entry in get_knowledge_index():
        score = 0
        topical_relevance = 0
        if (mode in entry.modes) or (mode == "crisis" and entry.source == "crisis"):
            score += 4

        if entry.source == "psychoeducation" and mode in {
            "assessment",
            "support",
            "intervention",
        }:
            score += 3

        if entry.source == "foundation" and mode in {"support", "assessment"}:
            score += 3

        if entry.source == "active_learning" and mode in {"support", "assessment"}:
            score += 2

        if mode == "support" and entry.source in {
            "cbt_exercise",
            "act_exercise",
            "dbt_exercise",
        }:
            score -= 4

        if mode == "assessment" and entry.source in {
            "cbt_exercise",
            "act_exercise",
            "dbt_exercise",
        }:
            score -= 2

        if entry.entry_id.startswith("local:"):
            score += 2

        if entry.entry_id.startswith("learning:") and mode in {
            "support",
            "assessment",
            "planning",
        }:
            score += 2

        if entry.entry_id.startswith("fetched:"):
            score -= 1

        if entry.source == "active_learning" and mode != "intervention":
            score += 1

        topic_hits = sum(1 for topic in entry.topics if topic in topics)
        score += topic_hits * 5
        topical_relevance += topic_hits

        keyword_hits = sum(
            1 for keyword in entry.keywords if keyword and _contains_keyword(normalized, compact, keyword)
        )
        score += min(keyword_hits, 4)
        topical_relevance += keyword_hits

        title_hits = sum(1 for token in _topic_keywords(entry.topics) if _contains_keyword(normalized, compact, token))
        if title_hits and _contains_keyword(normalized, compact, entry.title):
            score += 3
            topical_relevance += 1

        if mode == "crisis" and entry.source == "crisis":
            score += 3
        if risk_level in {"high", "critical"} and entry.source == "crisis":
            score += 3
        if mode == "crisis" and entry.entry_id == f"crisis:{risk_level}":
            score += 8
        if entry.source == "crisis" and mode != "crisis" and risk_level not in {"high", "critical"}:
            score -= 6
        if topical_relevance == 0 and mode != "crisis" and entry.source != "crisis":
            continue
        if score > 0:
            scored.append((score, entry))

    scored.sort(key=lambda item: (-item[0], item[1].entry_id))

    selected: list[KnowledgeEntry] = []
    seen_ids: set[str] = set()
    for _, entry in scored:
        if entry.entry_id in seen_ids:
            continue
        selected.append(entry)
        seen_ids.add(entry.entry_id)
        if len(selected) >= limit:
            break
    return selected
