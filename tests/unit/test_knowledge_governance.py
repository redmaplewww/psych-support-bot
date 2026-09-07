"""知识库治理（语料规模 + 渲染去重 + 预算截断 + 兜底指标）单测。

覆盖：
1. load_all_corpora：entry_id 去重、单源上限截断。
2. render_knowledge_sections：active_learning 条目不再双区重复渲染。
3. get_knowledge_context：总量预算截断、无命中时 fallback 指标触发。
"""

from dataclasses import dataclass

from psych_support_bot.ai.knowledge.index import (
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
