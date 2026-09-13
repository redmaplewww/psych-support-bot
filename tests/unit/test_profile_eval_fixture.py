"""画像提取评测集（金标准 fixture）结构校验——纯本地，无 LLM 调用。

校验对象：tests/evals/profile_extraction_cases.json（提取器实现前的合成对话金标准，
标注依据 docs/plans/profile-memory-knowledge.md §2/§5/§7 与
docs/technical/PROFILE_DECISIONS.md）。

约束的契约要点：
- key 闭集：锚点表 key ∪ D1 主题 key（import 校验，不硬编码清单）；
  唯一边界例外是 D4——锚点表不覆盖干预响应维度，其 key 用练习库真实 tag
  （ai/tools/exercises.py 的 cbt_*/act_*/dbt_*/sleep_*/panic_* 闭集）。
- distortion.* 命名空间（identify_only，红线 §7.2）：绝不出现在 expected_claims，
  只允许出现在红线 case 的 identify_only_keys 断言语境。
- 危机负例：expected_claims 必须为空且 forbidden 非空（画像层零存储）。
- 证据面：evidence_turn_indices 必须指向 role=user 的话轮（知识/助手话轮
  不构成用户状态证据）。
"""

import json
from pathlib import Path

import pytest

from psych_support_bot.ai.knowledge.index import TOPIC_KEYWORDS, detect_topics
from psych_support_bot.ai.profile.anchors import all_anchors
from psych_support_bot.ai.tools.exercises import list_all_exercises

_FIXTURE_PATH = Path(__file__).resolve().parent.parent / "evals" / "profile_extraction_cases.json"
_FIXTURE = json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))
_CASES: list[dict] = _FIXTURE["cases"]

# --- 闭集来源（全部 import，不硬编码） ---

_ANCHOR_KEYS = {a.key for a in all_anchors()}
_DEPOSITABLE_ANCHOR_KEYS = {a.key for a in all_anchors() if not a.identify_only}
_DISTORTION_KEYS = {a.key for a in all_anchors() if a.identify_only}
_TOPIC_KEYS = set(TOPIC_KEYWORDS)
_PRACTICE_TAGS = {f"{family}_{name}" for family, names in list_all_exercises().items() for name in names}

# --- fixture 自身契约 ---

_CASE_FIELDS = {"case_id", "language", "category", "turns", "expected_claims", "forbidden"}
_CLAIM_FIELDS = {"dimension", "key", "claim_zh", "evidence_turn_indices", "confidence_band", "relation"}
_CONFIDENCE_BANDS = {"low", "medium", "high"}
_DIMENSIONS = {"D1", "D2", "D3", "D4", "D5"}
_CATEGORY_PREFIXES = ("d1_", "d2_", "d3_", "d4_", "d5_", "mixed_", "neg_", "redline_")
_NEGATIVE_PREFIXES = ("neg_", "redline_")


def _user_texts(case: dict) -> str:
    return " ".join(turn["content"] for turn in case["turns"] if turn["role"] == "user")


# --- 顶层结构 ---


def test_fixture_loads_with_meta_and_size() -> None:
    assert set(_FIXTURE) >= {"_meta", "cases"}
    assert isinstance(_FIXTURE["_meta"].get("schema_version"), int)
    assert 25 <= len(_CASES) <= 35


def test_case_ids_unique() -> None:
    ids = [case["case_id"] for case in _CASES]
    assert len(ids) == len(set(ids))


def test_case_fields_and_enums() -> None:
    for case in _CASES:
        cid = case["case_id"]
        assert set(case) <= _CASE_FIELDS | {"identify_only_keys"}, f"{cid}: 出现未知字段"
        assert set(case) >= _CASE_FIELDS, f"{cid}: 缺必填字段"
        assert case["language"] in {"zh", "en"}, cid
        assert case["category"].startswith(_CATEGORY_PREFIXES), f"{cid}: category 不在闭集前缀内"
        assert case["case_id"].startswith(case["category"].split("_")[0] + "_"), (
            f"{cid}: case_id 与 category 前缀不一致"
        )


# --- turns 结构 ---


@pytest.mark.parametrize("case", _CASES, ids=[case["case_id"] for case in _CASES])
def test_turns_structure(case: dict) -> None:
    turns = case["turns"]
    assert 1 <= len(turns) <= 6, f"{case['case_id']}: 轮次须在 1-6 之间"
    assert any(turn["role"] == "user" for turn in turns), f"{case['case_id']}: 缺用户话轮"
    for i, turn in enumerate(turns):
        assert set(turn) == {"role", "content"}, f"{case['case_id']} turns[{i}]: 字段不符"
        assert turn["role"] in {"user", "assistant"}, f"{case['case_id']} turns[{i}]: role 非法"
        assert isinstance(turn["content"], str) and turn["content"].strip(), (
            f"{case['case_id']} turns[{i}]: content 为空"
        )


# --- expected_claims 形状与 key 闭集 ---


@pytest.mark.parametrize("case", _CASES, ids=[case["case_id"] for case in _CASES])
def test_claim_shape_and_key_closedset(case: dict) -> None:
    cid = case["case_id"]
    for claim in case["expected_claims"]:
        assert set(claim) == _CLAIM_FIELDS, f"{cid}: claim 字段必须恰为 {_CLAIM_FIELDS}"
        assert claim["dimension"] in _DIMENSIONS, f"{cid}: dimension 越界"
        assert claim["confidence_band"] in _CONFIDENCE_BANDS, f"{cid}: confidence_band 非法"
        assert claim["relation"] == "supports", f"{cid}: relation 固定为 supports"
        assert isinstance(claim["claim_zh"], str) and claim["claim_zh"].strip(), f"{cid}: claim_zh 为空"

        key = claim["key"]
        dimension = claim["dimension"]
        assert not key.startswith("distortion."), f"{cid}: distortion.* 绝不允许出现在 expected_claims"
        if dimension == "D1":
            assert key in _TOPIC_KEYS, f"{cid}: D1 key {key!r} 不在 TOPIC_KEYWORDS 闭集"
        elif dimension == "D2":
            assert key in _DEPOSITABLE_ANCHOR_KEYS | _TOPIC_KEYS, f"{cid}: D2 key {key!r} 越出锚点∪主题闭集"
        elif dimension in {"D3", "D5"}:
            assert key in _DEPOSITABLE_ANCHOR_KEYS, f"{cid}: {dimension} key {key!r} 不是可沉淀锚点 key"
        elif dimension == "D4":
            assert key in _PRACTICE_TAGS, f"{cid}: D4 key {key!r} 不是练习库真实 tag"


@pytest.mark.parametrize("case", _CASES, ids=[case["case_id"] for case in _CASES])
def test_evidence_indices_point_to_user_turns(case: dict) -> None:
    cid = case["case_id"]
    turns = case["turns"]
    for claim in case["expected_claims"]:
        indices = claim["evidence_turn_indices"]
        assert isinstance(indices, list) and indices, f"{cid}: evidence_turn_indices 必须为非空数组"
        assert len(indices) == len(set(indices)), f"{cid}: evidence 下标重复"
        for idx in indices:
            assert type(idx) is int, f"{cid}: evidence 下标必须是 int"
            assert 0 <= idx < len(turns), f"{cid}: evidence 下标 {idx} 越出 turns 范围"
            assert turns[idx]["role"] == "user", f"{cid}: evidence 下标 {idx} 指向非用户话轮（证据面只能是用户原话）"


# --- distortion.* 命名空间隔离（红线 §7.2） ---


def _iter_strings(value) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [s for item in value for s in _iter_strings(item)]
    if isinstance(value, dict):
        return [s for item in value.values() for s in _iter_strings(item)]
    return []


@pytest.mark.parametrize("case", _CASES, ids=[case["case_id"] for case in _CASES])
def test_distortion_namespace_isolation(case: dict) -> None:
    cid = case["case_id"]
    identify_only_keys = case.get("identify_only_keys", [])
    if identify_only_keys:
        assert cid.startswith("redline_"), f"{cid}: identify_only_keys 只允许出现在红线 case"
        assert set(identify_only_keys) <= _DISTORTION_KEYS, f"{cid}: identify_only_keys 含未知 distortion key"
    for text in _iter_strings({k: v for k, v in case.items() if k != "identify_only_keys"}):
        if "distortion." in text:
            assert text in identify_only_keys, f"{cid}: distortion.* 出现在断言语境之外：{text!r}"


def test_distortion_redline_case_present_and_plain_redline_case_present() -> None:
    distortion_cases = [c for c in _CASES if c.get("identify_only_keys")]
    assert len(distortion_cases) >= 1, "缺少使用 identify_only 识别路径的歪曲红线 case"
    for case in distortion_cases:
        assert case["expected_claims"] == [], f"{case['case_id']}: identify_only 场景不得有可沉淀 claim"
    plain_redlines = [c for c in _CASES if c["case_id"].startswith("redline_") and not c.get("identify_only_keys")]
    assert len(plain_redlines) >= 1, "缺少不含 distortion 语境的第二类红线 case"


# --- 负例与红线：零 claim + forbidden 完整 ---


def test_negative_and_redline_cases_are_zero_claim() -> None:
    neg_cases = [c for c in _CASES if c["case_id"].startswith(_NEGATIVE_PREFIXES)]
    assert len(neg_cases) >= 4
    for case in neg_cases:
        assert case["expected_claims"] == [], f"{case['case_id']}: 负例/红线 case 的 expected_claims 必须为空数组"
        forbidden = case["forbidden"]
        assert forbidden["reason"] and forbidden["note"], f"{case['case_id']}: forbidden 的 reason/note 不得为空"


def test_crisis_negative_case_zero_storage() -> None:
    crisis = [c for c in _CASES if c["category"] == "neg_crisis"]
    assert len(crisis) >= 1, "缺少危机负例 case"
    for case in crisis:
        assert case["expected_claims"] == [], f"{case['case_id']}: 危机内容必须 0 claims（画像层零存储）"
        assert case["forbidden"], f"{case['case_id']}: 危机负例的 forbidden 非空"


@pytest.mark.parametrize("case", _CASES, ids=[case["case_id"] for case in _CASES])
def test_forbidden_field_shape(case: dict) -> None:
    forbidden = case["forbidden"]
    assert set(forbidden) == {"reason", "note"}, f"{case['case_id']}: forbidden 字段须恰为 reason+note"
    assert all(isinstance(v, str) and v.strip() for v in forbidden.values()), f"{case['case_id']}: forbidden 存在空值"


# --- 正例：claim 预算与混合维度 ---


def test_positive_cases_have_claims_within_budget() -> None:
    for case in _CASES:
        if case["case_id"].startswith(_NEGATIVE_PREFIXES):
            continue
        claims = case["expected_claims"]
        assert 1 <= len(claims) <= 3, f"{case['case_id']}: 正例须有 1-3 条 claim（§5 单次提取 ≤3）"


def test_mixed_case_spans_multiple_dimensions() -> None:
    mixed = [c for c in _CASES if c["case_id"].startswith("mixed_")]
    assert len(mixed) >= 1, "缺少多维度混合 case"
    for case in mixed:
        dimensions = {claim["dimension"] for claim in case["expected_claims"]}
        assert len(dimensions) >= 2, f"{case['case_id']}: 混合 case 至少覆盖两个维度"


# --- a-g 类型覆盖 ---


def test_category_coverage_buckets() -> None:
    def _count(prefix: str) -> int:
        return sum(1 for case in _CASES if case["case_id"].startswith(prefix))

    # a-e：各正例维度下限（a/b/c/d ≥3，e ≥3 且含 waiting_for_readiness 一例）
    for prefix in ("d1_", "d2_", "d3_", "d4_", "d5_"):
        assert _count(prefix) >= 3, f"{prefix} 正例不足 3 个"
    # f：负例 ≥4（含危机）；g：红线 ≥2
    assert _count("neg_") >= 4
    assert _count("redline_") >= 2
    assert _count("mixed_") >= 1


def test_d2_covers_worry_and_three_sleep_types() -> None:
    d2_claims = [claim for case in _CASES if case["case_id"].startswith("d2_") for claim in case["expected_claims"]]
    worry = [c for c in d2_claims if c["key"] == "worry_uncontrollable"]
    sleep = [c for c in d2_claims if c["key"] == "sleep"]
    assert len(worry) >= 1, "D2 缺担忧失控（GAD-7 条目 2 语义）正例"
    assert len(sleep) >= 3, "D2 睡眠三型（入睡困难/易醒/早醒）须各有一例（以 claim_zh 文本区分）"


def test_d5_covers_waiting_for_readiness() -> None:
    d5_keys = {
        claim["key"] for case in _CASES if case["case_id"].startswith("d5_") for claim in case["expected_claims"]
    }
    assert "waiting_for_readiness" in d5_keys, "D5 缺 waiting_for_readiness 正例"


def test_d1_covers_semantic_only_and_keyword_hit_paths() -> None:
    """D1 必须同时考察语义提取与词表命中：语义 case 的主题 key 不得被
    detect_topics（生产关键词检测）命中，词表 case 必须被命中。"""
    semantic_cases: list[str] = []
    keyword_cases: list[str] = []
    for case in _CASES:
        if not case["case_id"].startswith(("d1_", "mixed_")):
            continue
        expected = {c["key"] for c in case["expected_claims"] if c["dimension"] == "D1"}
        if not expected:
            continue
        detected = set(detect_topics(_user_texts(case)))
        if expected & detected:
            keyword_cases.append(case["case_id"])
        else:
            semantic_cases.append(case["case_id"])
    assert len(semantic_cases) >= 2, f"关键词不出现但语义明确的 D1 case 不足 2 个：{semantic_cases}"
    assert len(keyword_cases) >= 1, f"含主题词表命中的 D1 case 至少 1 个：{keyword_cases}"


# --- 语言配比 ---


def test_zh_cases_at_least_two_thirds() -> None:
    zh_count = sum(1 for case in _CASES if case["language"] == "zh")
    assert 3 * zh_count >= 2 * len(_CASES), f"zh case 占比须 ≥ 2/3，当前 {zh_count}/{len(_CASES)}"
