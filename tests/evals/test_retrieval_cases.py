"""检索链路回归评测（纯本地，不调 LLM）。

用例分两层：
- must_pass：当前必须满足的检索行为（含批次 1 已修复的场景），
  回归即测试失败。
- known_gaps：已确认、排期由批次 2（LLM 语义 topics）修复的缺口。
  修复前用 xfail 标记防止被遗忘；批次 2 落地后应把它们移入 must_pass。

用例数据在 tests/evals/retrieval_cases.json，与本文件同目录。
"""

import json
from collections import Counter
from pathlib import Path

import pytest

from psych_support_bot.ai.knowledge.index import (
    detect_topics,
    retrieve_knowledge_entries,
)

_CASES_PATH = Path(__file__).parent / "retrieval_cases.json"
_CASES = json.loads(_CASES_PATH.read_text(encoding="utf-8"))


def _doc_key(entry_id: str) -> str:
    parts = entry_id.split(":")
    return ":".join(parts[:2]) if len(parts) >= 2 else entry_id


def _check(case: dict, entries) -> list[str]:
    """跑单条检索用例，返回失败原因列表（空 = 通过）。"""
    failures: list[str] = []
    entry_ids = [entry.entry_id for entry in entries]

    if case.get("expect_zero_hits") and entry_ids:
        failures.append(f"expected zero hits, got {entry_ids}")

    min_hits = case.get("min_hits")
    if min_hits is not None and len(entries) < min_hits:
        failures.append(f"expected >= {min_hits} hits, got {len(entries)}: {entry_ids}")

    expect_source = case.get("expect_source")
    if expect_source and not any(entry.source == expect_source for entry in entries):
        failures.append(f"no entry with source={expect_source} in {entry_ids}")

    expect_prefix = case.get("expect_entry_prefix")
    if expect_prefix and not any(eid.startswith(expect_prefix) for eid in entry_ids):
        failures.append(f"no entry id starting with {expect_prefix!r} in {entry_ids}")

    forbid_prefix = case.get("forbid_entry_prefix")
    if forbid_prefix and any(eid.startswith(forbid_prefix) for eid in entry_ids):
        failures.append(f"forbidden prefix {forbid_prefix!r} matched in {entry_ids}")

    max_per_doc = case.get("max_per_doc")
    if max_per_doc is not None:
        doc_counts = Counter(_doc_key(entry.entry_id) for entry in entries)
        flooded = {doc: n for doc, n in doc_counts.items() if n > max_per_doc}
        if flooded:
            failures.append(f"same-source flood beyond {max_per_doc}: {flooded}")

    expect_topic = case.get("expect_topic")
    if expect_topic and expect_topic not in detect_topics(case["message"]):
        failures.append(f"topic {expect_topic!r} not detected")

    return failures


@pytest.mark.parametrize(
    "case",
    _CASES["must_pass"],
    ids=[case["name"] for case in _CASES["must_pass"]],
)
def test_retrieval_must_pass(case: dict) -> None:
    entries = retrieve_knowledge_entries(case["message"], mode=case["mode"], risk_level=case["risk"], limit=5)
    failures = _check(case, entries)
    assert not failures, f"{case['name']}: {failures}"


@pytest.mark.xfail(
    reason="批次2 LLM 语义 topics 落地后转 must_pass",
    strict=True,
)
@pytest.mark.parametrize(
    "case",
    _CASES["known_gaps"],
    ids=[case["name"] for case in _CASES["known_gaps"]],
)
def test_retrieval_known_gaps(case: dict) -> None:
    entries = retrieve_knowledge_entries(case["message"], mode=case["mode"], risk_level=case["risk"], limit=5)
    failures = _check(case, entries)
    assert not failures, f"{case['name']}: {failures}"
