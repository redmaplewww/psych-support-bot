import json
from pathlib import Path

from psych_support_bot.ai.knowledge.index import (
    KnowledgeEntry,
    build_knowledge_index,
    detect_topics,
    get_knowledge_index,
    retrieve_knowledge_entries,
)
from psych_support_bot.ai.tools.knowledge_base import get_knowledge_context


def test_detect_topics_prioritizes_relevant_matches() -> None:
    topics = detect_topics("I feel burned out, exhausted, and keep procrastinating because I am overwhelmed.")

    assert "burnout" in topics
    assert "procrastination" in topics


def test_retrieve_entries_returns_indexed_matches() -> None:
    entries = retrieve_knowledge_entries(
        "I keep having panic attacks at night and I am scared to sleep.",
        mode="intervention",
        risk_level="low",
        limit=4,
    )

    entry_ids = {entry.entry_id for entry in entries}
    assert any(entry_id.startswith("dbt-exercise:tipp_full") for entry_id in entry_ids)
    assert any(entry_id.startswith("psychoeducation:panic_attacks_overview") for entry_id in entry_ids)


def test_intervention_context_includes_indexed_knowledge() -> None:
    context = get_knowledge_context(
        mode="intervention",
        risk_level="low",
        user_message="I keep overthinking and having panic attacks at night.",
    )

    assert "Detected topics:" in context
    assert "Grounded references:" in context
    assert "DBT TIPP" in context


def test_crisis_context_includes_indexed_resources() -> None:
    context = get_knowledge_context(
        mode="crisis",
        risk_level="high",
        user_message="I don't want to be here anymore.",
    )

    assert "Grounded references:" in context
    assert "988 Suicide and Crisis Lifeline" in context


def test_build_index_includes_local_runtime_corpus(monkeypatch) -> None:
    class _Chunk:
        entry_id = "local:cbt_sleep_manual:1"
        title = "CBT Sleep Manual (Chunk 1)"
        publisher = "local"
        topics = ("sleep",)
        modes = ("support", "assessment", "intervention", "planning")
        keywords = ("sleep", "insomnia")
        treatment_modalities = ("cbt_i",)
        audience = ("adult",)
        chapter_hint = "Chapter 3 Sleep Restriction"
        content = "Local CBT-I chapter discussing stimulus control and sleep restriction."
        url = "local://cbt_sleep_manual.pdf"

    monkeypatch.setattr(
        "psych_support_bot.ai.knowledge.index.load_all_corpora",
        lambda: [_Chunk()],
    )
    monkeypatch.setattr(
        "psych_support_bot.ai.knowledge.index.load_learning_notes",
        list,
    )

    entries = build_knowledge_index()

    assert any(entry.entry_id.startswith("local:") for entry in entries)


def test_build_index_includes_active_learning_notes(monkeypatch) -> None:
    class _Note:
        note_id = "learning:anxiety"
        title = "Active Learning Note: Anxiety"
        topics = ("anxiety",)
        modes = ("support", "assessment", "intervention")
        keywords = ("anxiety", "worry", "cbt")
        source_ids = ("nimh_anxiety", "local_anxiety_manual")
        source_count = 2
        treatment_modalities = ("cbt",)
        audience = ("adult",)
        summary = "Anxiety is often maintained by avoidance, and CBT can help through exposure and skills practice."
        practice_points = ("CBT can help through exposure and skills practice.",)

    monkeypatch.setattr(
        "psych_support_bot.ai.knowledge.index.load_all_corpora",
        list,
    )
    monkeypatch.setattr(
        "psych_support_bot.ai.knowledge.index.load_learning_notes",
        lambda: [_Note()],
    )

    entries = build_knowledge_index()

    assert any(entry.entry_id == "learning:anxiety" for entry in entries)


def test_context_surfaces_synthesized_takeaways(monkeypatch) -> None:
    monkeypatch.setattr(
        "psych_support_bot.ai.tools.knowledge_base.retrieve_knowledge_entries",
        lambda user_message, mode, risk_level, limit=5: [
            KnowledgeEntry(
                entry_id="learning:anxiety",
                title="Active Learning Note: Anxiety",
                source="active_learning",
                topics=("anxiety",),
                modes=("support",),
                keywords=("anxiety",),
                summary="Anxiety is often maintained by avoidance.",
                content="Anxiety is often maintained by avoidance.",
                action_hint="Synthesized from 3 sources",
            )
        ],
    )

    context = get_knowledge_context(
        mode="support",
        risk_level="low",
        user_message="I am anxious all the time.",
    )

    assert "Synthesized takeaways:" in context
    assert "Anxiety is often maintained by avoidance." in context


def test_detect_topics_supports_chinese_queries() -> None:
    topics = detect_topics("我有惊恐发作，心跳很快，晚上也睡不着")

    assert "panic" in topics
    assert "sleep" in topics


def test_retrieve_entries_for_chinese_query_returns_relevant_matches() -> None:
    entries = retrieve_knowledge_entries(
        "我有惊恐发作，心跳很快，晚上睡不着",
        mode="intervention",
        risk_level="low",
        limit=4,
    )

    entry_ids = {entry.entry_id for entry in entries}
    assert any("panic_attacks_overview" in entry_id for entry_id in entry_ids)
    assert "crisis:low" not in entry_ids


def test_support_mode_prefers_psychoeducation_over_exercises() -> None:
    entries = retrieve_knowledge_entries(
        "I feel anxious and cannot sleep lately",
        mode="support",
        risk_level="low",
        limit=3,
    )

    assert entries
    assert entries[0].source in {"psychoeducation", "foundation", "active_learning"}


def test_knowledge_index_refreshes_when_corpus_signature_changes(
    monkeypatch,
) -> None:
    signatures = iter([(("first", True, 1, 1),), (("second", True, 2, 2),)])
    builds = [
        [
            KnowledgeEntry(
                entry_id="foundation:first",
                title="First",
                source="foundation",
                topics=("stress",),
                modes=("support",),
                keywords=("stress",),
                summary="First entry",
                content="First entry",
            )
        ],
        [
            KnowledgeEntry(
                entry_id="foundation:second",
                title="Second",
                source="foundation",
                topics=("stress",),
                modes=("support",),
                keywords=("stress",),
                summary="Second entry",
                content="Second entry",
            )
        ],
    ]
    build_iter = iter(builds)

    monkeypatch.setattr(
        "psych_support_bot.ai.knowledge.index._knowledge_index_signature",
        lambda: next(signatures),
    )
    monkeypatch.setattr(
        "psych_support_bot.ai.knowledge.index.build_knowledge_index",
        lambda: next(build_iter),
    )
    monkeypatch.setattr(
        "psych_support_bot.ai.knowledge.index._KNOWLEDGE_INDEX_CACHE",
        None,
    )
    monkeypatch.setattr(
        "psych_support_bot.ai.knowledge.index._KNOWLEDGE_INDEX_SIGNATURE",
        (),
    )

    first = get_knowledge_index()
    second = get_knowledge_index()

    assert first[0].entry_id == "foundation:first"
    assert second[0].entry_id == "foundation:second"


def test_knowledge_index_refreshes_from_updated_corpus_files(
    monkeypatch,
    tmp_path: Path,
) -> None:
    def write_local_corpus(entry_id: str, title: str) -> None:
        (tmp_path / "local_corpus.json").write_text(
            json.dumps(
                [
                    {
                        "entry_id": entry_id,
                        "title": title,
                        "source_id": "local_test",
                        "publisher": "local_document",
                        "url": "folder/doc.md",
                        "topics": ["stress"],
                        "modes": ["support"],
                        "keywords": ["stress"],
                        "source_type": "local_document",
                        "treatment_modalities": [],
                        "audience": ["adult"],
                        "chapter_hint": "",
                        "content": title,
                    }
                ],
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    (tmp_path / "public_corpus.json").write_text("[]", encoding="utf-8")
    (tmp_path / "ingested_corpus.json").write_text("[]", encoding="utf-8")
    (tmp_path / "learning_notes.json").write_text("[]", encoding="utf-8")
    write_local_corpus("local:first:1", "First entry")

    monkeypatch.setattr(
        "psych_support_bot.ai.knowledge.index.knowledge_data_dir",
        lambda: tmp_path,
    )
    monkeypatch.setattr(
        "psych_support_bot.knowledge_ingestion.knowledge_data_dir",
        lambda: tmp_path,
    )
    monkeypatch.setattr(
        "psych_support_bot.ai.knowledge.index._KNOWLEDGE_INDEX_CACHE",
        None,
    )
    monkeypatch.setattr(
        "psych_support_bot.ai.knowledge.index._KNOWLEDGE_INDEX_SIGNATURE",
        (),
    )

    first = get_knowledge_index(force_reload=True)
    assert any(entry.entry_id == "local:first:1" for entry in first)

    write_local_corpus("local:second:1", "Second entry")
    second = get_knowledge_index()

    assert any(entry.entry_id == "local:second:1" for entry in second)
    assert not any(entry.entry_id == "local:first:1" for entry in second)
