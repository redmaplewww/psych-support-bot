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
        # B1: focus/concentration keywords merged into procrastination (knowledge freeze)
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
        # B1: Move behavioral-execution focus keywords to procrastination
        "注意力不集中",
        "专注不了",
        "看不进去",
        "注意力涣散",
        "无法专心",
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
        # B1: Focus keywords that are motivation-related (internal drive)
        "没干劲",
        "不想做",
        "缺乏动力",
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
    # 调节/放松意图：非危机的"想缓缓"类消息此前检不出任何主题，知识层
    # 无从接手。词汇聚焦意图（想放松/平静下来），不与 panic 的生理症状词
    # 重叠，避免惊恐发作消息被分流到放松内容。
    "relaxation": [
        "relax",
        "relaxation",
        "calm down",
        "calming",
        "soothing",
        "unwind",
        "decompress",
        "breathing exercise",
        "mindfulness",
        "meditation",
        "放松",
        "平静",
        "静一静",
        "缓缓",
        "缓一缓",
        "平复",
        "安定下来",
        "松口气",
        "舒压",
        "正念",
        "冥想",
        "深呼吸",
        "呼吸练习",
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

    # 调节/放松类指针条目：练习库里的稳定化练习（tools/exercises.py）此前
    # 不在知识检索通道——"我想放松一下"这类消息既检不出主题，索引里也没
    # 有可命中的条目。这里为最对口的三个练习建"指针"条目：不复制练习全
    # 文，交付载体仍是练习面板（action_hint 指向 tag），保持单一事实源。
    _RELAXATION_POINTER_EXERCISES: tuple[tuple[str, str, str, tuple[str, ...], str, str], ...] = (
        (
            "panic_grounding_5_4_3_2_1",
            "5-4-3-2-1 Grounding",
            "5-4-3-2-1 接地练习",
            ("relaxation", "panic", "anxiety"),
            "Anchors attention to the present through the five senses.",
            "用五感把注意力锚回当下，从慌乱里稳住。",
        ),
        (
            "sleep_wind_down",
            "Sleep Wind-Down Routine",
            "睡前收尾流程",
            ("relaxation", "sleep"),
            "A consistent pre-sleep ritual that signals the body it is time to rest.",
            "一套固定的睡前流程，给身体一个'该休息了'的信号。",
        ),
        (
            "dbt_tipp",
            "DBT TIPP",
            "DBT TIPP 降温",
            ("relaxation", "panic", "anger"),
            "Rapid physiological down-regulation for intense emotion spikes.",
            "高强度情绪飙升时的快速生理降温手段。",
        ),
    )
    for _tag, _name_en, _name_zh, _topics, _desc_en, _desc_zh in _RELAXATION_POINTER_EXERCISES:
        entries.append(
            _entry(
                entry_id=f"practice-pointer:{_tag}",
                title=f"{_name_en} / {_name_zh}",
                source="practice_pointer",
                topics=_topics,
                # support 是放松请求的主通道；intervention 便于引导时直接引用。
                modes=("support", "intervention"),
                keywords=(*_topic_keywords(_topics), "relax", "calm", "放松", "平静"),
                content=f"{_desc_en} {_desc_zh}",
                action_hint=(
                    f"Available in the Exercises panel as tag '{_tag}'. "
                    "Offer to walk through it together, or point the user there."
                ),
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
                # 语料条目与策展条目同口径截断（_entry 内部再截 summary）：
                # 全站抓取的长文整段进 prompt 会挤占回复预算。
                content=_clip(chunk.content, 1200),
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


# 同源多样性上限与 doc 键规则（retrieve_knowledge_entries 使用）：
# entry_id 形如 fetched:{doc}:chunkN / public:{doc}:{section}，前两段即册子键。
_MAX_PER_SOURCE_DOC = 2


def _source_doc_key(entry_id: str) -> str:
    parts = entry_id.split(":")
    return ":".join(parts[:2]) if len(parts) >= 2 else entry_id


def retrieve_knowledge_entries(
    user_message: str,
    mode: str,
    risk_level: str,
    *,
    limit: int = 4,
    extra_topics: list[str] | None = None,
) -> list[KnowledgeEntry]:
    # LLM 语义 topics 排前（闭集校验过，精度更高），关键词 topics 兜底在后。
    topics = list(dict.fromkeys([*(extra_topics or []), *detect_topics(user_message)]))
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
    # 同源多样性：168 条外部切片本就来自 15 个册子，同一手册的兄弟 chunk
    # （如 nimh_social_anxiety_brochure 的 chunk 1/10/11/12）内容高度重叠，
    # 不加控制会把 limit 名额刷成单册子专场。每册最多保留 2 条，其余名额
    # 让给排序中后续的不同来源。
    doc_counts: dict[str, int] = {}
    for _, entry in scored:
        if entry.entry_id in seen_ids:
            continue
        doc_key = _source_doc_key(entry.entry_id)
        if doc_counts.get(doc_key, 0) >= _MAX_PER_SOURCE_DOC:
            continue
        selected.append(entry)
        seen_ids.add(entry.entry_id)
        doc_counts[doc_key] = doc_counts.get(doc_key, 0) + 1
        if len(selected) >= limit:
            break
    return selected


# ---------------------------------------------------------------------------
# 渲染层（P3-B 统一：检索与渲染同层，tools/knowledge_base 只留门面）
# ---------------------------------------------------------------------------


def render_entry(entry_id: str, title: str, summary: str, action_hint: str) -> str:
    rendered = f"{entry_id}: {title}. {summary}"
    if action_hint:
        rendered += f" Action hint: {action_hint}"
    return rendered


def render_knowledge_sections(entries: list[KnowledgeEntry]) -> list[str]:
    """把检索结果渲染为分区文本段（takeaways / psychoeducation / grounded）。

    原 tools/knowledge_base._grouped_entries_text 迁此——渲染与检索同层，
    门面只做组装、预算与埋点。
    """
    learning_entries = [entry for entry in entries if entry.source == "active_learning"]
    # active_learning 已单独渲染进 Synthesized takeaways 时不再重复进
    # Psychoeducation notes——同一 entry 双区渲染既冗余又挤占预算。
    psychoeducation_sources = {"psychoeducation", "foundation"} | (set() if learning_entries else {"active_learning"})
    psychoeducation_entries = [entry for entry in entries if entry.source in psychoeducation_sources]
    grounded_entries = [
        entry for entry in entries if entry.source not in {"active_learning", "psychoeducation", "foundation"}
    ]
    sections: list[str] = []
    # 知识区语气指令：这些是给模型化用的背景，不是给用户播报的资料——
    # "Grounded references" 式渲染头会诱发'根据资料说'的播报腔（机械感根因之一）。
    usage_note = (
        "Background for you to weave into your own empathic wording — never cite sources, "
        "list references, or announce 'according to...' in the visible reply:"
    )

    if learning_entries:
        rendered_learning = [
            render_entry(entry.entry_id, entry.title, entry.summary, entry.action_hint)
            for entry in learning_entries[:2]
        ]
        sections.append("Synthesized takeaways: " + " ".join(rendered_learning))

    if psychoeducation_entries:
        rendered_psychoeducation = [
            render_entry(entry.entry_id, entry.title, entry.summary, entry.action_hint)
            for entry in psychoeducation_entries[:3]
        ]
        sections.append("Psychoeducation notes: " + " ".join(rendered_psychoeducation))

    if grounded_entries:
        rendered_grounded = [
            render_entry(entry.entry_id, entry.title, entry.summary, entry.action_hint)
            for entry in grounded_entries[:5]
        ]
        sections.append(usage_note + " " + " ".join(rendered_grounded))

    return sections
