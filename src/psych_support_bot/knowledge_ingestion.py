from __future__ import annotations

import ipaddress
import json
import os
import re
import socket
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

try:
    from pypdf import PdfReader
except Exception:  # pragma: no cover - optional at import time
    PdfReader = None


USER_AGENT = "psych-support-bot-knowledge-ingestor/0.2"
LOCAL_IMPORT_EXTENSIONS = {".txt", ".md", ".json", ".pdf"}
# 语料规模治理（红线执行机制）：语料文件是唯一能绕过代码评审增长
# 知识索引的入口，单来源超过上限的部分直接丢弃，防全站抓取挤占
# 索引与检索信噪比。策展内容（代码内置知识）不受此限制。
MAX_CHUNKS_PER_SOURCE = 200
TOPIC_HINTS = {
    "anxiety": ["anxiety", "gad", "worry", "fear", "phobia"],
    "panic": ["panic"],
    "depression": ["depression", "depressive", "low_mood"],
    "sleep": ["sleep", "insomnia", "cbt-i"],
    "ocd": ["ocd", "obsessive", "compulsive", "erp"],
    "stress": ["stress", "ptsd", "trauma"],
    "burnout": ["burnout"],
    "relationships": ["relationship", "couple", "family", "attachment"],
    "self_worth": ["shame", "self_criticism", "self-esteem"],
    "motivation": ["motivation", "procrastination", "avoidance"],
}
MODALITY_HINTS = {
    "cbt": ["cbt", "cognitive behavioral", "thought record", "behavioral activation"],
    "act": ["act", "acceptance and commitment", "defusion", "values"],
    "dbt": ["dbt", "tipp", "wise mind", "dear man", "distress tolerance"],
    "erp": ["erp", "exposure and response prevention"],
    "mi": ["motivational interviewing", "change talk", "oars"],
    "sfbt": ["solution-focused", "miracle question", "scaling question"],
    "cbt_i": ["cbt-i", "stimulus control", "sleep restriction"],
}
AUDIENCE_HINTS = {
    "adult": ["adult", "adults"],
    "child": ["child", "children", "adolescent", "teen"],
    "caregiver": ["caregiver", "parent", "family"],
    "clinician": ["clinician", "therapist", "provider", "professional"],
}


@dataclass(frozen=True)
class SourceSpec:
    source_id: str
    title: str
    publisher: str
    url: str
    topics: tuple[str, ...]
    modes: tuple[str, ...]
    content_type: str


@dataclass(frozen=True)
class CorpusChunk:
    entry_id: str
    title: str
    source_id: str
    publisher: str
    url: str
    topics: tuple[str, ...]
    modes: tuple[str, ...]
    keywords: tuple[str, ...]
    content: str
    source_type: str = "unknown"
    treatment_modalities: tuple[str, ...] = ()
    audience: tuple[str, ...] = ()
    chapter_hint: str = ""


@dataclass(frozen=True)
class LearningNote:
    note_id: str
    title: str
    topics: tuple[str, ...]
    modes: tuple[str, ...]
    keywords: tuple[str, ...]
    source_ids: tuple[str, ...]
    source_count: int
    treatment_modalities: tuple[str, ...] = ()
    audience: tuple[str, ...] = ()
    summary: str = ""
    practice_points: tuple[str, ...] = ()


class _HTMLTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._parts: list[str] = []
        self._ignored_tag_stack: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        if tag in {"script", "style", "noscript"}:
            self._ignored_tag_stack.append(tag)

    def handle_endtag(self, tag: str) -> None:
        if self._ignored_tag_stack and self._ignored_tag_stack[-1] == tag:
            self._ignored_tag_stack.pop()

    def handle_data(self, data: str) -> None:
        if self._ignored_tag_stack:
            return
        cleaned = data.strip()
        if cleaned:
            self._parts.append(cleaned)

    def text(self) -> str:
        return "\n".join(self._parts)


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def knowledge_data_dir() -> Path:
    return repo_root() / "data" / "knowledge"


def knowledge_local_drop_dir() -> Path:
    return knowledge_data_dir() / "local_drop"


def _read_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def _corpus_path(name: str) -> Path:
    return knowledge_data_dir() / name


def _write_json_atomic(path: Path, payload: object) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    os.replace(temp_path, path)
    return path


def _serialize_chunk(chunk: CorpusChunk) -> dict[str, object]:
    return {
        "entry_id": chunk.entry_id,
        "title": chunk.title,
        "source_id": chunk.source_id,
        "publisher": chunk.publisher,
        "url": chunk.url,
        "topics": list(chunk.topics),
        "modes": list(chunk.modes),
        "keywords": list(chunk.keywords),
        "source_type": chunk.source_type,
        "treatment_modalities": list(chunk.treatment_modalities),
        "audience": list(chunk.audience),
        "chapter_hint": chunk.chapter_hint,
        "content": chunk.content,
    }


def _string_tuple(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(str(item) for item in value)


def _deserialize_chunk(item: dict[str, object]) -> CorpusChunk:
    return CorpusChunk(
        entry_id=str(item["entry_id"]),
        title=str(item["title"]),
        source_id=str(item["source_id"]),
        publisher=str(item["publisher"]),
        url=str(item["url"]),
        topics=_string_tuple(item.get("topics")),
        modes=_string_tuple(item.get("modes")),
        keywords=_string_tuple(item.get("keywords")),
        source_type=str(item.get("source_type", "unknown")),
        treatment_modalities=_string_tuple(item.get("treatment_modalities")),
        audience=_string_tuple(item.get("audience")),
        chapter_hint=str(item.get("chapter_hint", "")),
        content=str(item["content"]),
    )


def _deserialize_learning_note(item: dict[str, object]) -> LearningNote:
    source_count = item.get("source_count", 0)
    return LearningNote(
        note_id=str(item["note_id"]),
        title=str(item["title"]),
        topics=_string_tuple(item.get("topics")),
        modes=_string_tuple(item.get("modes")),
        keywords=_string_tuple(item.get("keywords")),
        source_ids=_string_tuple(item.get("source_ids")),
        source_count=int(source_count) if isinstance(source_count, int | str) else 0,
        treatment_modalities=_string_tuple(item.get("treatment_modalities")),
        audience=_string_tuple(item.get("audience")),
        summary=str(item.get("summary", "")),
        practice_points=_string_tuple(item.get("practice_points")),
    )


def load_source_registry() -> list[SourceSpec]:
    payload = _read_json(_corpus_path("source_registry.json"))
    assert isinstance(payload, list)
    return [
        SourceSpec(
            source_id=str(item["source_id"]),
            title=str(item["title"]),
            publisher=str(item["publisher"]),
            url=str(item["url"]),
            topics=tuple(str(value) for value in item.get("topics", [])),
            modes=tuple(str(value) for value in item.get("modes", [])),
            content_type=str(item.get("content_type", "public_web")),
        )
        for item in payload
        if isinstance(item, dict)
    ]


def _load_corpus_file(path: Path) -> list[CorpusChunk]:
    if not path.exists():
        return []
    payload = _read_json(path)
    if not isinstance(payload, list):
        return []
    return [_deserialize_chunk(item) for item in payload if isinstance(item, dict)]


def load_public_corpus() -> list[CorpusChunk]:
    return _load_corpus_file(_corpus_path("public_corpus.json"))


def load_ingested_corpus() -> list[CorpusChunk]:
    return _load_corpus_file(_corpus_path("ingested_corpus.json"))


def load_local_corpus() -> list[CorpusChunk]:
    return _load_corpus_file(_corpus_path("local_corpus.json"))


def load_learning_notes() -> list[LearningNote]:
    path = _corpus_path("learning_notes.json")
    if not path.exists():
        return []
    payload = _read_json(path)
    if not isinstance(payload, list):
        return []
    return [_deserialize_learning_note(item) for item in payload if isinstance(item, dict)]


def load_all_corpora() -> list[CorpusChunk]:
    """加载全部语料并做规模治理：entry_id 去重 + 单来源上限（MAX_CHUNKS_PER_SOURCE）。"""
    seen: set[str] = set()
    deduped: list[CorpusChunk] = []
    for chunk in [*load_public_corpus(), *load_local_corpus(), *load_ingested_corpus()]:
        if chunk.entry_id in seen:
            continue
        seen.add(chunk.entry_id)
        deduped.append(chunk)
    per_source: dict[str, int] = {}
    capped: list[CorpusChunk] = []
    for chunk in deduped:
        count = per_source.get(chunk.publisher, 0)
        if count >= MAX_CHUNKS_PER_SOURCE:
            continue
        per_source[chunk.publisher] = count + 1
        capped.append(chunk)
    return capped


def _assert_public_http_url(url: str) -> None:
    """Reject non-http(s) schemes and hosts that resolve to loopback/private/reserved addresses (SSRF guard)."""
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError(f"URL scheme not allowed: {parsed.scheme or '(none)'}")
    if not parsed.hostname:
        raise ValueError("URL must include a hostname")
    try:
        addr_infos = socket.getaddrinfo(parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80))
    except socket.gaierror as exc:  # pragma: no cover - depends on DNS
        raise ValueError(f"URL host does not resolve: {parsed.hostname}") from exc
    for info in addr_infos:
        ip = ipaddress.ip_address(info[4][0])
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            raise ValueError(f"URL host resolves to a blocked address: {parsed.hostname}")


def fetch_url_text(url: str, timeout: int = 30) -> str:
    _assert_public_http_url(url)
    request = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(request, timeout=timeout) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        return response.read().decode(charset, errors="ignore")


def html_to_text(html: str) -> str:
    boilerplate_markers = (
        "skip to main content",
        "an official website of the united states government",
        "here’s how you know",
        "official websites use .gov",
        "secure .gov websites use https",
        "share page",
        "menu",
        "privacy policy",
        "website policies",
        "federal resources",
        "policies and notices",
        "nimh resources",
        "connect with nimh",
        "funding & grant news",
        "innovation speaker series",
        "stakeholder engagement",
        "staff directories",
        "about medlineplus",
        "site map",
        "customer support",
        "return to top",
        "transforming the understanding and treatment of mental illnesses",
        "digital shareables",
        "science education",
        "research funded by nimh",
        "research conducted at nimh",
        "priority research areas",
        "resources for researchers",
        "opportunities & announcements",
        "application process",
        "managing grants",
        "clinical research",
        "training",
        "small business research",
        "meetings and events",
        "multimedia",
        "about the acting nimh director",
        "advisory boards and groups",
        "offices and divisions",
        "careers at nimh",
        "website belongs to an official government organization",
        "safely connected to the .gov website",
        "due to current hhs and nih restructuring",
        "help for mental illnesses información en español",
        "mental health information home",
        "you are here:",
        "quick links",
        "last reviewed:",
        "unless otherwise specified",
    )
    navigation_labels = {
        "mental health information",
        "get involved",
        "research",
        "funding",
        "news & events",
        "about us",
        "health topics",
        "statistics",
        "brochures and fact sheets",
        "help for mental illnesses",
        "clinical trials",
        "top",
        "disclaimer",
    }

    extractor = _HTMLTextExtractor()
    extractor.feed(html)
    lines: list[str] = []
    seen: set[str] = set()
    for line in extractor.text().splitlines():
        stripped = " ".join(line.split()).strip()
        lowered = stripped.lower()
        if not stripped:
            continue
        if lowered.startswith(("var ", "window.", "function(", "(function(")):
            continue
        if lowered.startswith(("home |", "menu", "search the nimh website")):
            continue
        if lowered in navigation_labels:
            continue
        if any(marker in lowered for marker in boilerplate_markers):
            continue
        if lowered.count(" | ") >= 2:
            continue
        if stripped.count("/") > 8:
            continue
        if lowered.startswith(("home >", "home ->", "home \x1a")):
            continue
        if lowered.endswith((" home", " menu")) and len(lowered) < 40:
            continue
        if len(stripped) < 20 and lowered not in {
            "summary",
            "treatment",
            "symptoms",
            "diagnosis",
        }:
            continue
        signature = re.sub(r"\W+", " ", lowered).strip()
        if signature in seen:
            continue
        seen.add(signature)
        lines.append(stripped)

    raw = "\n".join(lines)
    raw = re.sub(r"\n{3,}", "\n\n", raw)
    return raw.strip()


def _contains_hint(text: str, hint: str) -> bool:
    escaped = re.escape(hint.lower())
    if re.fullmatch(r"[a-z0-9_\- ]+", hint.lower()):
        pattern = rf"(?<![a-z]){escaped}(?![a-z])"
        return re.search(pattern, text.lower()) is not None
    return escaped in text.lower()


def chunk_text(text: str, *, chunk_size: int = 900, overlap: int = 120) -> list[str]:
    normalized = " ".join(text.split())
    if not normalized:
        return []
    if len(normalized) <= chunk_size:
        return [normalized]

    chunks: list[str] = []
    start = 0
    while start < len(normalized):
        end = min(start + chunk_size, len(normalized))
        if end < len(normalized):
            boundary = normalized.rfind(". ", start, end)
            if boundary > start + (chunk_size // 2):
                end = boundary + 1
        chunk = normalized[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= len(normalized):
            break
        start = max(end - overlap, 0)
    return chunks


def build_chunks_from_source(spec: SourceSpec, text: str) -> list[CorpusChunk]:
    chunks = chunk_text(text)
    topic_keywords = tuple(spec.topics)
    chapter_hint = spec.title
    modalities = _infer_treatment_modalities(text, Path(spec.title))
    audience: tuple[str, ...] = ()
    keywords = (*topic_keywords, *modalities, *audience)
    return [
        CorpusChunk(
            entry_id=f"fetched:{spec.source_id}:{index}",
            title=f"{spec.title} (Chunk {index})",
            source_id=spec.source_id,
            publisher=spec.publisher,
            url=spec.url,
            topics=spec.topics,
            modes=spec.modes,
            keywords=keywords,
            source_type=spec.content_type,
            treatment_modalities=modalities,
            audience=audience,
            chapter_hint=chapter_hint,
            content=chunk,
        )
        for index, chunk in enumerate(chunks, start=1)
    ]


def _sanitize_id(value: str) -> str:
    sanitized = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
    return sanitized or "document"


def _infer_topics(text: str, path: Path) -> tuple[str, ...]:
    lowered = f"{path.stem} {text[:2000]}".lower()
    matches = [topic for topic, hints in TOPIC_HINTS.items() if any(hint in lowered for hint in hints)]
    return tuple(matches[:4]) or ("stress",)


def _infer_treatment_modalities(text: str, path: Path) -> tuple[str, ...]:
    lowered = f"{path.stem} {text[:3000]}".lower()
    matches = [
        modality for modality, hints in MODALITY_HINTS.items() if any(_contains_hint(lowered, hint) for hint in hints)
    ]
    return tuple(matches[:4])


def _infer_audience(text: str, path: Path) -> tuple[str, ...]:
    lowered = f"{path.stem} {text[:3000]}".lower()
    matches = [
        audience for audience, hints in AUDIENCE_HINTS.items() if any(_contains_hint(lowered, hint) for hint in hints)
    ]
    return tuple(matches[:3]) or ("adult",)


def _infer_chapter_hint(text: str, path: Path) -> str:
    for line in text.splitlines()[:20]:
        stripped = line.strip().lstrip("#").strip()
        if len(stripped) >= 8:
            return stripped[:120]
    chapter_match = re.search(r"chapter\s+\d+[\w\s:-]*", text[:4000], re.IGNORECASE)
    if chapter_match:
        return chapter_match.group(0)[:120]
    return path.stem.replace("_", " ").replace("-", " ")[:120]


def _infer_modes(topics: tuple[str, ...]) -> tuple[str, ...]:
    if "crisis" in topics:
        return ("crisis", "support")
    return ("support", "assessment", "intervention", "planning")


def _json_to_text(payload: object) -> str:
    if isinstance(payload, str):
        return payload
    if isinstance(payload, (int, float, bool)):
        return str(payload)
    if isinstance(payload, list):
        return "\n".join(_json_to_text(item) for item in payload)
    if isinstance(payload, dict):
        parts: list[str] = []
        for key, value in payload.items():
            value_text = _json_to_text(value)
            if value_text.strip():
                parts.append(f"{key}: {value_text}")
        return "\n".join(parts)
    return ""


def _extract_text_from_pdf(path: Path) -> str:
    if PdfReader is None:
        return ""
    reader = PdfReader(str(path))
    pages = [page.extract_text() or "" for page in reader.pages]
    return "\n".join(page for page in pages if page.strip())


def _extract_text_from_local_file(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in {".txt", ".md"}:
        return path.read_text(encoding="utf-8", errors="ignore")
    if suffix == ".json":
        return _json_to_text(_read_json(path))
    if suffix == ".pdf":
        return _extract_text_from_pdf(path)
    return ""


def _serialize_learning_note(note: LearningNote) -> dict[str, object]:
    return {
        "note_id": note.note_id,
        "title": note.title,
        "topics": list(note.topics),
        "modes": list(note.modes),
        "keywords": list(note.keywords),
        "source_ids": list(note.source_ids),
        "source_count": note.source_count,
        "treatment_modalities": list(note.treatment_modalities),
        "audience": list(note.audience),
        "summary": note.summary,
        "practice_points": list(note.practice_points),
    }


def _clip(text: str, limit: int = 320) -> str:
    compact = " ".join(text.split())
    if len(compact) <= limit:
        return compact
    return f"{compact[: limit - 3].rstrip()}..."


def _split_sentences(text: str) -> list[str]:
    normalized = " ".join(text.split())
    if not normalized:
        return []
    parts = re.split(r"(?<=[.!?])\s+", normalized)
    sentences: list[str] = []
    for part in parts:
        sentence = part.strip()
        if len(sentence) < 40:
            continue
        first = sentence.lstrip("\"'([{- ")[:1]
        if first and first.isalpha() and not first.isupper():
            continue
        if sentence.count(":") > 3:
            continue
        sentences.append(sentence)
    return sentences


def _score_sentence(sentence: str, *, focus_terms: set[str]) -> int:
    lowered = sentence.lower()
    score = 1
    score += sum(3 for term in focus_terms if term and term in lowered)
    score += sum(
        2
        for cue in (
            "effective",
            "helps",
            "can help",
            "treatment",
            "therapy",
            "skills",
            "research",
            "recommended",
            "includes",
            "support",
        )
        if cue in lowered
    )
    if any(noise in lowered for noise in ("copyright", "privacy", "clinical trial", "share")):
        score -= 3
    return score


def _dedupe_preserve_order(items: list[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    ordered: list[str] = []
    for item in items:
        cleaned = item.strip()
        signature = re.sub(r"\W+", " ", cleaned.lower()).strip()
        if not cleaned or signature in seen:
            continue
        seen.add(signature)
        ordered.append(cleaned)
    return tuple(ordered)


def _build_learning_summary(chunks: list[CorpusChunk], topic: str) -> tuple[str, tuple[str, ...]]:
    focus_terms = {topic.replace("_", " ")}
    for chunk in chunks:
        focus_terms.update(chunk.topics)
        focus_terms.update(chunk.treatment_modalities)
        focus_terms.update(chunk.keywords)

    sentences: list[str] = []
    for chunk in chunks:
        sentences.extend(_split_sentences(chunk.content))

    ranked = sorted(
        sentences,
        key=lambda sentence: (
            -_score_sentence(sentence, focus_terms=focus_terms),
            len(sentence),
        ),
    )
    top_sentences = _dedupe_preserve_order(ranked[:3])
    summary = _clip(" ".join(top_sentences), 500)

    practice_candidates = [
        sentence
        for sentence in ranked
        if any(
            cue in sentence.lower()
            for cue in (
                "can help",
                "helps",
                "treatment",
                "therapy",
                "practice",
                "support",
                "skills",
                "includes",
            )
        )
    ]
    practice_points = _dedupe_preserve_order([_clip(sentence, 180) for sentence in practice_candidates[:3]])
    return summary, practice_points


def synthesize_learning_notes(chunks: list[CorpusChunk]) -> list[LearningNote]:
    grouped: dict[str, list[CorpusChunk]] = {}
    for chunk in chunks:
        for topic in chunk.topics:
            if topic in {"support"}:
                continue
            grouped.setdefault(topic, []).append(chunk)

    notes: list[LearningNote] = []
    for topic, topic_chunks in sorted(grouped.items()):
        if len(topic_chunks) < 2:
            continue
        summary, practice_points = _build_learning_summary(topic_chunks, topic)
        if not summary:
            continue

        source_ids = _dedupe_preserve_order([chunk.source_id for chunk in topic_chunks])
        modalities = _dedupe_preserve_order([item for chunk in topic_chunks for item in chunk.treatment_modalities])
        audience = _dedupe_preserve_order([item for chunk in topic_chunks for item in chunk.audience])
        modes = _dedupe_preserve_order([item for chunk in topic_chunks for item in chunk.modes])
        keywords = _dedupe_preserve_order([topic, *topic.replace("_", " ").split(), *modalities, *audience])
        notes.append(
            LearningNote(
                note_id=f"learning:{topic}",
                title=f"Active Learning Note: {topic.replace('_', ' ').title()}",
                topics=(topic,),
                modes=modes or ("support", "assessment", "intervention", "planning"),
                keywords=keywords,
                source_ids=source_ids,
                source_count=len(source_ids),
                treatment_modalities=modalities,
                audience=audience,
                summary=summary,
                practice_points=practice_points,
            )
        )
    return notes


def write_learning_notes(
    chunks: list[CorpusChunk],
    output_path: Path | None = None,
) -> Path:
    notes = synthesize_learning_notes(chunks)
    destination = output_path or _corpus_path("learning_notes.json")
    return _write_json_atomic(
        destination,
        [_serialize_learning_note(note) for note in notes],
    )


def discover_local_documents(base_dir: Path | None = None) -> list[Path]:
    root = base_dir or knowledge_local_drop_dir()
    if not root.exists():
        return []
    return sorted(
        path
        for path in root.rglob("*")
        if path.is_file()
        and path.suffix.lower() in LOCAL_IMPORT_EXTENSIONS
        and not path.name.lower().startswith("readme")
    )


def build_chunks_from_local_document(path: Path, base_dir: Path | None = None) -> list[CorpusChunk]:
    text = _extract_text_from_local_file(path)
    chunks = chunk_text(text)
    if not chunks:
        return []

    root = base_dir or knowledge_local_drop_dir()
    relative = path.relative_to(root) if path.is_relative_to(root) else path.name
    relative_key = str(relative).replace("\\", "/")
    source_id = f"local_{_sanitize_id(relative_key)}"
    topics = _infer_topics(text, path)
    modes = _infer_modes(topics)
    modalities = _infer_treatment_modalities(text, path)
    audience = _infer_audience(text, path)
    chapter_hint = _infer_chapter_hint(text, path)
    keywords = (*topics, *modalities, *audience)
    return [
        CorpusChunk(
            entry_id=f"local:{source_id}:{index}",
            title=f"{path.stem} (Chunk {index})",
            source_id=source_id,
            publisher="local_document",
            url=str(relative),
            topics=topics,
            modes=modes,
            keywords=keywords,
            source_type="local_document",
            treatment_modalities=modalities,
            audience=audience,
            chapter_hint=chapter_hint,
            content=chunk,
        )
        for index, chunk in enumerate(chunks, start=1)
    ]


def ingest_registry(output_path: Path | None = None) -> Path:
    specs = load_source_registry()
    corpus: list[dict[str, object]] = []
    failures: list[dict[str, str]] = []
    raw_dir = knowledge_data_dir() / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)

    for spec in specs:
        try:
            html = fetch_url_text(spec.url)
        except (HTTPError, URLError, TimeoutError) as exc:
            failures.append({"source_id": spec.source_id, "url": spec.url, "error": str(exc)})
            continue

        (raw_dir / f"{spec.source_id}.html").write_text(html, encoding="utf-8")
        chunks = build_chunks_from_source(spec, html_to_text(html))
        corpus.extend(_serialize_chunk(chunk) for chunk in chunks)

    destination = output_path or _corpus_path("ingested_corpus.json")
    _write_json_atomic(destination, corpus)
    report = {
        "fetched_sources": sorted({str(item["source_id"]) for item in corpus}),
        "chunk_count": len(corpus),
        "failures": failures,
    }
    _write_json_atomic(_corpus_path("ingestion_report.json"), report)
    return destination


def ingest_local_documents(
    source_dir: Path | None = None,
    output_path: Path | None = None,
) -> Path:
    root = source_dir or knowledge_local_drop_dir()
    root.mkdir(parents=True, exist_ok=True)
    documents = discover_local_documents(root)
    corpus: list[dict[str, object]] = []
    imported_files: list[str] = []
    skipped_files: list[dict[str, str]] = []

    for path in documents:
        try:
            chunks = build_chunks_from_local_document(path, root)
        except Exception as exc:
            skipped_files.append({"path": str(path), "reason": str(exc)})
            continue
        if not chunks:
            skipped_files.append({"path": str(path), "reason": "no_extractable_text"})
            continue
        imported_files.append(str(path.relative_to(root)))
        corpus.extend(_serialize_chunk(chunk) for chunk in chunks)

    destination = output_path or _corpus_path("local_corpus.json")
    _write_json_atomic(destination, corpus)
    _write_json_atomic(
        _corpus_path("local_import_report.json"),
        {
            "source_dir": str(root),
            "document_count": len(imported_files),
            "imported_files": imported_files,
            "chunk_count": len(corpus),
            "skipped_files": skipped_files,
        },
    )
    return destination


def ingest_all() -> dict[str, Path]:
    remote_path = ingest_registry()
    local_path = ingest_local_documents()
    notes_path = write_learning_notes(load_all_corpora())
    return {
        "remote": remote_path,
        "local": local_path,
        "notes": notes_path,
    }


def main() -> None:
    outputs = ingest_all()
    print(f"Remote corpus written to {outputs['remote']}")
    print(f"Local corpus written to {outputs['local']}")
    print(f"Learning notes written to {outputs['notes']}")
