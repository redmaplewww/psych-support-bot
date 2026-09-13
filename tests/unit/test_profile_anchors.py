"""画像锚点表一致性单测。

锚点表（ai/profile/anchors.py）是知识库的编译视图：本文件钉住
live-bind 不脱钩、双语完整性、identify_only 红线命名空间、与 D1 主题
词表的命名空间隔离。改动锚点表前先读 docs/technical/PROFILE_DECISIONS.md。
"""

from psych_support_bot.ai.knowledge.act import ACT_CORE_PROCESSES
from psych_support_bot.ai.knowledge.cbt import COGNITIVE_DISTORTIONS
from psych_support_bot.ai.knowledge.foundations import FOUNDATIONAL_KNOWLEDGE
from psych_support_bot.ai.knowledge.index import TOPIC_KEYWORDS as _INDEX_TOPICS
from psych_support_bot.ai.knowledge.sfbt_mi import MI_TOOLS
from psych_support_bot.ai.profile import anchors as anchors_module
from psych_support_bot.ai.profile.anchors import (
    ANCHOR_DIMENSIONS,
    TOPIC_KEYWORDS,
    all_anchors,
    anchor_by_id,
    anchors_for_dimension,
)
from psych_support_bot.domain.assessments.questionnaires import QUESTIONNAIRES

# --- 结构不变量 ---


def test_anchor_ids_unique() -> None:
    anchors = all_anchors()
    ids = [a.anchor_id for a in anchors]
    assert len(ids) == len(set(ids))


def test_anchor_dimensions_in_closed_set() -> None:
    for anchor in all_anchors():
        assert anchor.dimension in ANCHOR_DIMENSIONS


def test_bilingual_completeness() -> None:
    for anchor in all_anchors():
        assert anchor.en_phrases, f"{anchor.anchor_id} 缺英文短语"
        assert anchor.zh_phrases, f"{anchor.anchor_id} 缺中文短语"


def test_k0_coverage_counts() -> None:
    # 设计文档 §4 六项输入的编译产物规模。有人删锚会在这里显式失败，
    # 防止静默缩水；有意扩容时同步更新本断言。
    by_dimension = {dim: len(anchors_for_dimension(dim)) for dim in ANCHOR_DIMENSIONS}
    assert by_dimension == {"D2": 1, "D3": 16, "D5": 9}
    assert len(all_anchors()) == 26


# --- live-bind 不脱钩 ---


def test_act_phrases_live_bound_to_knowledge() -> None:
    for anchor in anchors_for_dimension("D3") + anchors_for_dimension("D5"):
        if not anchor.source.startswith("act:"):
            continue
        process = anchor.source.split(":")[1]
        assert anchor.en_phrases == tuple(ACT_CORE_PROCESSES[process]["typical_phrases"])


def test_distortion_examples_live_bound_and_identify_only() -> None:
    distortion_anchors = [a for a in all_anchors() if a.key.startswith("distortion.")]
    assert len(distortion_anchors) == len(COGNITIVE_DISTORTIONS)
    for anchor in distortion_anchors:
        distortion_id = anchor.source.split(":")[-1]
        assert anchor.en_phrases == tuple(COGNITIVE_DISTORTIONS[distortion_id]["examples"])
        assert anchor.identify_only is True


def test_safety_behavior_phrases_verbatim_in_source_text() -> None:
    source_text = FOUNDATIONAL_KNOWLEDGE["social_anxiety_basics"]["content"]
    anchor = anchor_by_id("D3.avoidance_maintenance.social")
    assert anchor is not None
    for phrase in anchor.en_phrases:
        assert phrase in source_text, f"{phrase!r} 不在 social_anxiety_basics 源文本中"


def test_change_talk_markers_verbatim_in_source() -> None:
    types = MI_TOOLS["change_talk"]["types"]
    for anchor in anchors_for_dimension("D5"):
        if not anchor.key.startswith("change_talk."):
            continue
        type_name = anchor.key.removeprefix("change_talk.").upper()
        for marker in anchor.en_phrases:
            assert marker in types[type_name], f"{marker!r} 不在 {type_name} 描述原文中"


def test_gad7_anchor_live_binds_questionnaire_item() -> None:
    anchor = anchor_by_id("D2.worry_uncontrollable")
    assert anchor is not None
    assert QUESTIONNAIRES["gad7"]["items"][1] in anchor.zh_phrases


# --- 红线与命名空间 ---


def test_identify_only_uses_distortion_namespace() -> None:
    # 红线 §7.2 的机器可判别标记：identify_only 锚必须以 distortion. 开头，
    # 反之该命名空间下的锚必须全部 identify_only。
    for anchor in all_anchors():
        if anchor.identify_only:
            assert anchor.key.startswith("distortion.")
        elif anchor.key.startswith("distortion."):
            raise AssertionError(f"{anchor.anchor_id} 可沉淀为信念，违反认知歪曲红线")


def test_anchor_keys_disjoint_from_topic_vocabulary() -> None:
    # D1（主题）与 D2/D3/D5（belief key）是两套命名空间：belief key 撞上
    # 主题词会让"换措辞复活"去重和维度路由发生歧义。
    topic_keys = set(TOPIC_KEYWORDS)
    for anchor in all_anchors():
        assert anchor.key not in topic_keys


def test_topic_reexport_is_knowledge_source_object() -> None:
    assert TOPIC_KEYWORDS is _INDEX_TOPICS


# --- 查询辅助 ---


def test_lookup_helpers_round_trip() -> None:
    for anchor in all_anchors():
        assert anchor_by_id(anchor.anchor_id) is anchor
    d5_ids = {a.anchor_id for a in anchors_for_dimension("D5")}
    assert "D5.sustain_talk" in d5_ids
    assert "D5.change_talk.desire" in d5_ids
    assert anchors_module.anchors_for_dimension("D1") == []
