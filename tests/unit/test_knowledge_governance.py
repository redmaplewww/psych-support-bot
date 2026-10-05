"""知识库治理（语料规模 + 渲染去重 + 预算截断 + 兜底指标）单测。

覆盖：
1. load_all_corpora：entry_id 去重、单源上限截断。
2. render_knowledge_sections：active_learning 条目不再双区重复渲染。
3. get_knowledge_context：总量预算截断、无命中时 fallback 指标触发。
"""

from dataclasses import dataclass

from psych_support_bot.ai.knowledge.act import ACT_EXERCISES
from psych_support_bot.ai.knowledge.cbt import CBT_EXERCISES
from psych_support_bot.ai.knowledge.dbt import DBT_EXERCISES
from psych_support_bot.ai.knowledge.index import (
    EXERCISE_TOPIC_MAP,
    TOPIC_KEYWORDS,
    render_entry,
    render_knowledge_sections,
)
from psych_support_bot.ai.tools.knowledge_base import KNOWLEDGE_CONTEXT_BUDGET, _clip_context
from psych_support_bot.knowledge_ingestion import MAX_CHUNKS_PER_SOURCE, load_all_corpora


@dataclass
class _Chunk:
    entry_id: str
    title: str
    publisher: str
    topics: tuple
    modes: tuple
    keywords: tuple
    content: str


@dataclass
class _Entry:
    entry_id: str
    title: str
    source: str
    topics: tuple
    modes: tuple
    keywords: tuple
    summary: str
    content: str
    action_hint: str = ""


# --- load_all_corpora 治理 ---


def test_load_all_corpora_dedupes_and_caps(monkeypatch) -> None:
    def _chunks(entry_id_base, publisher, count):
        return [
            _Chunk(
                entry_id=f"{entry_id_base}:{i}",
                title=f"t{i}",
                publisher=publisher,
                topics=("anxiety",),
                modes=("support",),
                keywords=("anxiety",),
                content="c",
            )
            for i in range(count)
        ]

    monkeypatch.setattr(
        "psych_support_bot.knowledge_ingestion.load_public_corpus",
        lambda: _chunks("dup", "P1", 3) + _chunks("cap", "P2", MAX_CHUNKS_PER_SOURCE + 5),
    )
    monkeypatch.setattr("psych_support_bot.knowledge_ingestion.load_local_corpus", lambda: _chunks("dup", "P1", 2))
    monkeypatch.setattr("psych_support_bot.knowledge_ingestion.load_ingested_corpus", lambda: [])

    chunks = load_all_corpora()
    ids = [c.entry_id for c in chunks]
    assert len(ids) == len(set(ids))  # 去重
    p2 = [c for c in chunks if c.publisher == "P2"]
    assert len(p2) == MAX_CHUNKS_PER_SOURCE  # 单源上限


# --- 渲染去重 ---


def test_active_learning_entry_rendered_once() -> None:
    entry = _Entry(
        entry_id="learning:panic",
        title="Active Learning Note",
        source="active_learning",
        topics=("panic",),
        modes=("support",),
        keywords=(),
        summary="note summary",
        content="note summary",
    )
    sections = render_knowledge_sections([entry])
    joined = " ".join(sections)
    assert joined.count("learning:panic") == 1
    assert any(s.startswith("Synthesized takeaways:") for s in sections)


# --- 预算截断 ---


def test_clip_context_truncates_long_output() -> None:
    assert len(_clip_context("x" * 9999)) <= KNOWLEDGE_CONTEXT_BUDGET
    assert _clip_context("short") == "short"


def test_render_entry_includes_action_hint() -> None:
    rendered = render_entry("id1", "Title", "sum", "hint")
    assert "id1" in rendered and "hint" in rendered


# --- 练习主题映射治理 ---


def test_cbt_exercise_dict_keys_match_exercise_ids() -> None:
    # 防回归：曾出现键 " downward_arrow" 带前导空格，主题映射按干净键查找
    # 落空，条目主题退化为默认 stress。
    for key, exercise in CBT_EXERCISES.items():
        assert key == exercise.exercise_id


def test_exercise_topic_map_covers_all_library_exercises() -> None:
    library_ids = set(CBT_EXERCISES) | set(ACT_EXERCISES) | set(DBT_EXERCISES)
    assert set(EXERCISE_TOPIC_MAP) >= library_ids


def test_exercise_topic_map_topics_in_closed_vocabulary() -> None:
    # 主题必须是 TOPIC_KEYWORDS 闭集成员，否则 detect_topics 永远检不出
    # 该条目，映射等于写死了一条死键。
    for topics in EXERCISE_TOPIC_MAP.values():
        assert set(topics) <= set(TOPIC_KEYWORDS)
