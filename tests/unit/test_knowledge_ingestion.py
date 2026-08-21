import json
from pathlib import Path

from psych_support_bot.knowledge_ingestion import (
    CorpusChunk,
    LearningNote,
    SourceSpec,
    build_chunks_from_local_document,
    build_chunks_from_source,
    chunk_text,
    discover_local_documents,
    html_to_text,
    ingest_local_documents,
    ingest_registry,
    load_all_corpora,
    load_learning_notes,
    load_public_corpus,
    load_source_registry,
    synthesize_learning_notes,
    write_learning_notes,
)


def test_load_source_registry_has_multiple_authoritative_sources() -> None:
    sources = load_source_registry()

    assert len(sources) >= 10
    assert any(source.source_id == "nimh_depression" for source in sources)
    assert any(source.source_id == "medlineplus_insomnia" for source in sources)


def test_load_public_corpus_has_seed_entries() -> None:
    corpus = load_public_corpus()

    assert len(corpus) >= 10
    assert any(entry.source_id == "nimh_anxiety_disorders" for entry in corpus)
    assert any(entry.source_id == "medlineplus_insomnia" for entry in corpus)


def test_chunk_text_splits_large_text_with_overlap() -> None:
    text = " ".join(f"sentence-{i}." for i in range(300))
    chunks = chunk_text(text, chunk_size=200, overlap=40)

    assert len(chunks) > 1
    assert all(len(chunk) <= 220 for chunk in chunks)


def test_ingest_registry_skips_failed_sources(monkeypatch, tmp_path: Path) -> None:
    specs = [
        SourceSpec(
            source_id="ok_source",
            title="OK Source",
            publisher="Test Publisher",
            url="https://example.com/ok",
            topics=("stress",),
            modes=("support",),
            content_type="public_web",
        ),
        SourceSpec(
            source_id="bad_source",
            title="Bad Source",
            publisher="Test Publisher",
            url="https://example.com/bad",
            topics=("stress",),
            modes=("support",),
            content_type="public_web",
        ),
    ]

    monkeypatch.setattr(
        "psych_support_bot.knowledge_ingestion.load_source_registry",
        lambda: specs,
    )

    def fake_fetch(url: str, timeout: int = 30) -> str:
        if url.endswith("bad"):
            raise TimeoutError("blocked")
        return (
            "<html><body><h1>Example</h1>"
            "<p>This is useful clinical content about stress recovery and coping skills for users.</p>"
            "<p>It is long enough to survive the cleaner and become a corpus chunk.</p>"
            "</body></html>"
        )

    monkeypatch.setattr(
        "psych_support_bot.knowledge_ingestion.fetch_url_text",
        fake_fetch,
    )
    monkeypatch.setattr(
        "psych_support_bot.knowledge_ingestion.knowledge_data_dir",
        lambda: tmp_path,
    )

    output_path = ingest_registry(output_path=tmp_path / "out.json")
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    report = json.loads((tmp_path / "ingestion_report.json").read_text(encoding="utf-8"))

    assert payload
    assert any(item["source_id"] == "ok_source" for item in payload)
    assert report["failures"][0]["source_id"] == "bad_source"


def test_discover_and_chunk_local_documents(tmp_path: Path, monkeypatch) -> None:
    drop = tmp_path / "local_drop"
    drop.mkdir()
    document = drop / "cbt_anxiety_notes.md"
    document.write_text(
        "# CBT Anxiety Notes\n\nThis chapter discusses anxiety maintenance, avoidance, and exposure principles. "
        "It includes enough detail to be chunked into the local corpus for retrieval.",
        encoding="utf-8",
    )

    monkeypatch.setattr(
        "psych_support_bot.knowledge_ingestion.knowledge_local_drop_dir",
        lambda: drop,
    )

    discovered = discover_local_documents(drop)
    chunks = build_chunks_from_local_document(document)

    assert discovered == [document]
    assert chunks
    assert chunks[0].entry_id.startswith("local:")
    assert "anxiety" in chunks[0].topics
    assert chunks[0].source_type == "local_document"
    assert chunks[0].chapter_hint
    assert "cbt" in chunks[0].keywords or chunks[0].treatment_modalities == ()


def test_ingest_local_documents_writes_local_corpus(tmp_path: Path, monkeypatch) -> None:
    drop = tmp_path / "local_drop"
    drop.mkdir()
    (drop / "sleep_manual.txt").write_text(
        "Insomnia is maintained by arousal, irregular schedules, and conditioned wakefulness. "
        "CBT-I usually includes stimulus control, sleep restriction, and cognitive restructuring.",
        encoding="utf-8",
    )

    monkeypatch.setattr(
        "psych_support_bot.knowledge_ingestion.knowledge_local_drop_dir",
        lambda: drop,
    )
    monkeypatch.setattr(
        "psych_support_bot.knowledge_ingestion.knowledge_data_dir",
        lambda: tmp_path,
    )

    output_path = ingest_local_documents(source_dir=drop, output_path=tmp_path / "local.json")
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    report = json.loads((tmp_path / "local_import_report.json").read_text(encoding="utf-8"))

    assert payload
    assert payload[0]["entry_id"].startswith("local:")
    assert report["document_count"] == 1


def test_local_documents_with_same_stem_get_distinct_ids(tmp_path: Path) -> None:
    drop = tmp_path / "local_drop"
    first_dir = drop / "set_a"
    second_dir = drop / "set_b"
    first_dir.mkdir(parents=True)
    second_dir.mkdir(parents=True)

    first = first_dir / "shared_notes.md"
    second = second_dir / "shared_notes.md"
    first.write_text(
        "Anxiety notes with enough content to create a retrievable chunk.",
        encoding="utf-8",
    )
    second.write_text(
        "Sleep notes with enough content to create a different retrievable chunk.",
        encoding="utf-8",
    )

    first_chunks = build_chunks_from_local_document(first, drop)
    second_chunks = build_chunks_from_local_document(second, drop)

    assert first_chunks
    assert second_chunks
    assert first_chunks[0].source_id != second_chunks[0].source_id
    assert first_chunks[0].entry_id != second_chunks[0].entry_id
    assert first_chunks[0].url == str(Path("set_a") / "shared_notes.md")
    assert second_chunks[0].url == str(Path("set_b") / "shared_notes.md")


def test_load_all_corpora_includes_local_and_public(monkeypatch) -> None:
    monkeypatch.setattr(
        "psych_support_bot.knowledge_ingestion.load_public_corpus",
        lambda: [
            CorpusChunk(
                entry_id="public:test:1",
                title="Public",
                source_id="public_test",
                publisher="NIMH",
                url="https://example.com/public",
                topics=("stress",),
                modes=("support",),
                keywords=("stress",),
                source_type="public_web",
                treatment_modalities=(),
                audience=("adult",),
                chapter_hint="Public",
                content="public content",
            )
        ],
    )
    monkeypatch.setattr(
        "psych_support_bot.knowledge_ingestion.load_local_corpus",
        lambda: [
            CorpusChunk(
                entry_id="local:test:1",
                title="Local",
                source_id="local_test",
                publisher="local",
                url="local://notes.txt",
                topics=("sleep",),
                modes=("intervention",),
                keywords=("sleep",),
                source_type="local_document",
                treatment_modalities=("cbt_i",),
                audience=("adult",),
                chapter_hint="Sleep Notes",
                content="local content",
            )
        ],
    )
    monkeypatch.setattr(
        "psych_support_bot.knowledge_ingestion.load_ingested_corpus",
        list,
    )

    corpus = load_all_corpora()

    assert len(corpus) == 2
    assert any(entry.entry_id.startswith("local:") for entry in corpus)


def test_html_to_text_removes_navigation_noise() -> None:
    html = """
    <html><body>
    <div>Skip to main content</div>
    <div>Home | Help for Mental Illnesses | Search the NIMH website</div>
    <h1>Depression</h1>
    <p>Depression can cause severe symptoms that affect sleep, appetite, and daily functioning.</p>
    <div>Meetings and Events</div>
    </body></html>
    """

    text = html_to_text(html)

    assert "Skip to main content" not in text
    assert "Meetings and Events" not in text
    assert "Depression can cause severe symptoms" in text


def test_html_to_text_removes_nimh_banner_and_repeated_nav() -> None:
    html = """
    <html><body>
    <div>Help for Mental Illnesses - National Institute of Mental Health (NIMH)</div>
    <div>An official website of the United States government</div>
    <div>Mental Health Information</div>
    <div>Help for Mental Illnesses</div>
    <div>Quick Links</div>
    <p>If you are suicidal or in emotional distress, consider using the 988 Suicide & Crisis Lifeline.</p>
    <p>A primary care provider can perform an initial mental health screening and refer you to a mental health professional.</p>
    </body></html>
    """

    text = html_to_text(html)

    assert "An official website of the United States government" not in text
    assert "Quick Links" not in text
    assert "988 Suicide & Crisis Lifeline" in text
    assert "primary care provider can perform an initial mental health screening" in text


def test_local_metadata_inference_avoids_false_act_match(tmp_path: Path, monkeypatch) -> None:
    drop = tmp_path / "local_drop"
    drop.mkdir()
    document = drop / "anxiety_notes.md"
    document.write_text(
        "# Anxiety Notes\n\nContact with family can help, but the main content is about anxiety and worry.",
        encoding="utf-8",
    )

    monkeypatch.setattr(
        "psych_support_bot.knowledge_ingestion.knowledge_local_drop_dir",
        lambda: drop,
    )

    chunks = build_chunks_from_local_document(document)

    assert chunks
    assert "act" not in chunks[0].treatment_modalities


def test_web_chunk_metadata_stays_conservative() -> None:
    spec = SourceSpec(
        source_id="nimh_test",
        title="Anxiety Disorders",
        publisher="NIMH",
        url="https://example.com/anxiety",
        topics=("anxiety",),
        modes=("support", "assessment"),
        content_type="public_web",
    )

    chunks = build_chunks_from_source(
        spec,
        "This page mentions family support resources, but the clinical topic is anxiety disorders.",
    )

    assert chunks
    assert chunks[0].audience == ()
    assert chunks[0].chapter_hint == "Anxiety Disorders"


def test_synthesize_learning_notes_builds_topic_level_summary() -> None:
    chunks = [
        CorpusChunk(
            entry_id="local:1",
            title="Sleep Manual (Chunk 1)",
            source_id="sleep_manual",
            publisher="local_document",
            url="local://sleep_manual.txt",
            topics=("sleep",),
            modes=("support", "intervention"),
            keywords=("sleep", "insomnia", "cbt_i"),
            source_type="local_document",
            treatment_modalities=("cbt_i",),
            audience=("adult",),
            chapter_hint="Chapter 2",
            content=(
                "Insomnia is often maintained by conditioned wakefulness and irregular schedules. "
                "CBT-I treatment includes stimulus control and sleep restriction, and it can help rebuild sleep drive."
            ),
        ),
        CorpusChunk(
            entry_id="public:1",
            title="Sleep Guide (Chunk 1)",
            source_id="sleep_public",
            publisher="NIMH",
            url="https://example.com/sleep",
            topics=("sleep",),
            modes=("support", "intervention", "planning"),
            keywords=("sleep", "insomnia"),
            source_type="public_web",
            treatment_modalities=("cbt_i",),
            audience=("adult",),
            chapter_hint="Overview",
            content=(
                "Behavioral sleep treatment helps people reduce time awake in bed and strengthen sleep cues. "
                "Consistent wake times and structured practice can help insomnia symptoms improve over time."
            ),
        ),
    ]

    notes = synthesize_learning_notes(chunks)

    assert notes
    sleep_note = next(note for note in notes if note.note_id == "learning:sleep")
    assert "CBT-I" in sleep_note.summary or "sleep" in sleep_note.summary.lower()
    assert sleep_note.source_count == 2
    assert "cbt_i" in sleep_note.treatment_modalities


def test_write_and_load_learning_notes_round_trip(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        "psych_support_bot.knowledge_ingestion.knowledge_data_dir",
        lambda: tmp_path,
    )
    chunks = [
        CorpusChunk(
            entry_id="local:1",
            title="Anxiety Notes (Chunk 1)",
            source_id="anxiety_notes",
            publisher="local_document",
            url="local://anxiety.txt",
            topics=("anxiety",),
            modes=("support", "assessment"),
            keywords=("anxiety", "worry"),
            source_type="local_document",
            treatment_modalities=("cbt",),
            audience=("adult",),
            chapter_hint="Overview",
            content=(
                "Anxiety can narrow attention and drive avoidance. CBT treatment can help people test feared predictions and reduce safety behaviors."
            ),
        ),
        CorpusChunk(
            entry_id="public:1",
            title="Anxiety Notes (Chunk 2)",
            source_id="anxiety_public",
            publisher="NIMH",
            url="https://example.com/anxiety",
            topics=("anxiety",),
            modes=("support", "intervention"),
            keywords=("anxiety", "worry"),
            source_type="public_web",
            treatment_modalities=("cbt",),
            audience=("adult",),
            chapter_hint="Overview",
            content=(
                "Structured coping skills, psychoeducation, and gradual behavior change can help anxiety symptoms improve."
            ),
        ),
    ]

    output = write_learning_notes(chunks, output_path=tmp_path / "learning_notes.json")
    notes = load_learning_notes()

    assert output.exists()
    assert notes
    assert isinstance(notes[0], LearningNote)
