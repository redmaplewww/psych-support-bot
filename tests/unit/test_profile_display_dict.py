"""画像展示词典一致性单测。

展示词典（ai/profile/display_dict.py）是 P3 决策下术语出不了存储层的唯一
渲染通道：本文件钉住 key 覆盖不脱钩、distortion.* 永不入典、临床术语黑名单、
维度分区标题闭集与查询辅助行为。改词典前先读
docs/technical/PROFILE_DECISIONS.md（P3）。
"""

from psych_support_bot.ai.knowledge.cbt import COGNITIVE_DISTORTIONS
from psych_support_bot.ai.knowledge.crisis import SAFETY_PLAN_TEMPLATE
from psych_support_bot.ai.knowledge.index import TOPIC_KEYWORDS
from psych_support_bot.ai.profile import display_dict
from psych_support_bot.ai.profile.anchors import all_anchors
from psych_support_bot.ai.profile.display_dict import (
    ALL_LABELS,
    DIMENSION_HEADERS,
    DIMENSIONS,
    DISPLAY_LABELS,
    dimension_header,
    friendly_label,
)

# 用户可见字符串的临床术语黑名单（P3 红线的机器可判别形式）。
# zh 按子串匹配；en 统一小写后按子串匹配。发现新措辞漏网时在这里补词。
_ZH_BLACKLIST: tuple[str, ...] = (
    "抑郁症",
    "焦虑症",
    "抑郁",
    "焦虑",
    "社恐",
    "恐慌症",
    "人格",
    "认知融合",
    "反刍",
    "灾难化",
    "读心术",
    "强迫",
    "创伤",
    "认知歪曲",
    "贴标签",
    "情绪化推理",
    "回避型",
    "回避",
    "进食障碍",
    "暴食",
    "厌食",
    "自伤",
    "自杀",
    "症状",
    "诊断",
    "临床",
    "病理",
    "精神障碍",
    "心理障碍",
    "解离",
)
_EN_BLACKLIST: tuple[str, ...] = (
    "depression",
    "depressed",
    "anxiety",
    "anxious",
    "panic",
    "ocd",
    "ptsd",
    "trauma",
    "disorder",
    "personality",
    "cognitive fusion",
    "fusion",
    "rumination",
    "catastrophiz",
    "mind reading",
    "mind-reading",
    "labeling",
    "emotional reasoning",
    "avoidant",
    "avoidance",
    "self-harm",
    "self harm",
    "suicid",
    "eating disorder",
    "symptom",
    "diagnos",
    "clinical",
    "patholog",
    "psycholog",
    "psychiatr",
    "therapy",
    "therapist",
    "obsession",
    "compulsion",
    "insomnia",
)

# 设计文档 §2 的维度取值规模（D1 主题 20、D2 锚 1、D3 可展示机制锚 5、
# D4 效果三值、D5 动机锚 9、D6 暂无可展示 key、D7 安全计划六节、D8 负记忆
# 分组）。有意扩容时显式更新本断言，防止静默漂移。
_EXPECTED_COUNTS: dict[str, int] = {
    "D1": 20,
    "D2": 1,
    "D3": 5,
    "D4": 3,
    "D5": 9,
    "D6": 0,
    "D7": 6,
    "D8": 1,
}


def _keys_for(dimension: str) -> set[str]:
    return {label.key for label in ALL_LABELS if label.dimension == dimension}


# --- 覆盖校验：与锚点表 / 主题词表不脱钩 ---


def test_d1_covers_exactly_topic_keyword_keys() -> None:
    assert _keys_for("D1") == set(TOPIC_KEYWORDS)


def test_d1_labels_bilingual() -> None:
    for topic in TOPIC_KEYWORDS:
        assert friendly_label(topic, "zh"), f"D1 主题 {topic} 缺中文标签"
        assert friendly_label(topic, "en"), f"D1 主题 {topic} 缺英文标签"


def test_all_non_distortion_anchor_keys_covered_with_matching_dimension() -> None:
    displayable = [a for a in all_anchors() if not a.key.startswith("distortion.")]
    assert displayable, "锚点表意外缩水，覆盖校验失去意义"
    for anchor in displayable:
        label = DISPLAY_LABELS.get(anchor.key)
        assert label is not None, f"锚 key {anchor.key} 无展示标签"
        assert label.dimension == anchor.dimension, f"{anchor.key} 维度标注与锚点表不一致"
        assert label.zh and label.en, f"{anchor.key} 标签双语不完整"


def test_label_keys_unique() -> None:
    assert len(ALL_LABELS) == len(DISPLAY_LABELS)


def test_coverage_counts_match_design() -> None:
    counts = {dim: len(_keys_for(dim)) for dim in DIMENSIONS}
    assert counts == _EXPECTED_COUNTS
    assert len(ALL_LABELS) == sum(_EXPECTED_COUNTS.values())


# --- 红线：distortion.* 永不展示 ---


def test_distortion_namespace_never_displayable() -> None:
    for distortion_id in COGNITIVE_DISTORTIONS:
        key = f"distortion.{distortion_id}"
        assert key not in DISPLAY_LABELS, f"{key} 严禁入典（identify_only 红线）"
        assert friendly_label(key, "zh") is None
        assert friendly_label(key, "en") is None


def test_no_distortion_prefixed_key_anywhere() -> None:
    assert not any(key.startswith("distortion.") for key in DISPLAY_LABELS)


# --- 红线：临床术语黑名单 ---


def test_no_clinical_terms_in_labels() -> None:
    for label in ALL_LABELS:
        for term in _ZH_BLACKLIST:
            assert term not in label.zh, f"{label.key} 中文标签含术语 {term!r}: {label.zh}"
            assert term not in label.en, f"{label.key} 英文标签含术语 {term!r}: {label.en}"
        lowered = label.en.lower()
        for term in _EN_BLACKLIST:
            assert term not in lowered, f"{label.key} 英文标签含术语 {term!r}: {label.en}"


def test_no_clinical_terms_in_dimension_headers() -> None:
    for header in DIMENSION_HEADERS.values():
        for term in _ZH_BLACKLIST:
            assert term not in header[0] and term not in header[1]
        lowered = header[1].lower()
        for term in _EN_BLACKLIST:
            assert term not in lowered, f"分区标题含术语 {term!r}: {header[1]}"


# --- 维度分区标题 ---


def test_dimension_headers_cover_closed_set_bilingually() -> None:
    assert set(DIMENSION_HEADERS) == set(DIMENSIONS)
    for dimension in DIMENSIONS:
        assert dimension_header(dimension, "zh"), f"{dimension} 缺中文分区标题"
        assert dimension_header(dimension, "en"), f"{dimension} 缺英文分区标题"


def test_dimension_header_normalizes_case_and_rejects_unknown() -> None:
    assert dimension_header("d1") == dimension_header("D1") == "最近在关注"
    assert dimension_header(" D4 ") == "对你有帮助的"
    assert dimension_header("D9") is None
    assert dimension_header("") is None
    assert dimension_header("distortion") is None


# --- 查询辅助 ---


def test_friendly_label_language_behavior() -> None:
    assert friendly_label("sleep", "zh") == "睡不好、睡不安稳"
    assert friendly_label("sleep", "en") == "Trouble sleeping"
    # 仅 "en" 视为英文（与 memory_modules 同口径），未知语言回退中文。
    assert friendly_label("sleep", "EN") == "Trouble sleeping"
    assert friendly_label("sleep", "fr") == friendly_label("sleep", "zh")
    assert friendly_label("sleep") == friendly_label("sleep", "zh")


def test_friendly_label_unknown_key_returns_none() -> None:
    assert friendly_label("nonexistent_key") is None
    assert friendly_label("") is None


def test_d4_effect_tri_value_wording_pinned() -> None:
    assert friendly_label("worked") == "用起来有帮助"
    assert friendly_label("neutral") == "感觉一般"
    assert friendly_label("aversive") == "用着不太合适"


# --- D7 与安全计划六节的绑定 ---


def test_protective_groups_match_safety_plan_sections() -> None:
    protective_keys = {key for key in DISPLAY_LABELS if key.startswith("protective.")}
    assert len(protective_keys) == len(SAFETY_PLAN_TEMPLATE["sections"]) == 6
    for key in protective_keys:
        assert DISPLAY_LABELS[key].dimension == "D7"
        assert friendly_label(key, "zh") and friendly_label(key, "en")


# --- 模块数据不可变（无 I/O 的 frozen 结构） ---


def test_public_mappings_are_read_only() -> None:
    import pytest

    with pytest.raises(TypeError):
        DISPLAY_LABELS["sneaky"] = DISPLAY_LABELS["worked"]  # type: ignore[index]
    with pytest.raises(TypeError):
        DIMENSION_HEADERS["D9"] = ("x", "y")  # type: ignore[index]
    assert display_dict.ALL_LABELS[0] is not None
