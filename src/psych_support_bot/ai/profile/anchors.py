"""画像提取锚点表——知识库的编译视图，不是第二个事实源。

设计约束（docs/plans/profile-memory-knowledge.md §4）：
- 英文短语尽量 **live-bind** 知识模块原文（ACT typical_phrases / MI
  change talk / 认知歪曲例句 / GAD-7 条目），知识文件更新时本表自动
  跟随；本表只补两样东西——中文等义扩展、知识库以行文形式存在而
  无法直接绑定的词锚（安全行为清单、sustain talk）。
- 锚点只负责降低提取器漏检率；最终判定由提取器 LLM 给出并附
  message_id 证据，锚点命中本身不构成信念。
- ``identify_only=True`` 的锚（认知歪曲类）只可用于当轮干预识别，
  **禁止沉淀为画像信念**（PROFILE_DECISIONS/红线 §7.2：人格盖章禁止）；
  机器可判别标记 = key 以 ``distortion.`` 命名空间开头。
- D1（关注主题）直接复用 TOPIC_KEYWORDS 闭集，本表 re-export 作统一
  导入面，不复制词表。
"""

from __future__ import annotations

from dataclasses import dataclass

from psych_support_bot.ai.knowledge.act import ACT_CORE_PROCESSES
from psych_support_bot.ai.knowledge.cbt import COGNITIVE_DISTORTIONS
from psych_support_bot.ai.knowledge.index import TOPIC_KEYWORDS
from psych_support_bot.domain.assessments.questionnaires import QUESTIONNAIRES

# 锚点表当前覆盖的画像维度闭集。D1 走 TOPIC_KEYWORDS（见上），D4 来自
# 结构化练习记录（practice tags），均不设词锚。
ANCHOR_DIMENSIONS = ("D2", "D3", "D5")


@dataclass(frozen=True)
class ProfileAnchor:
    """单条词锚：dimension + key 定位一个 belief 槽位，双语短语供召回。"""

    anchor_id: str
    dimension: str
    key: str
    en_phrases: tuple[str, ...]
    zh_phrases: tuple[str, ...]
    source: str
    identify_only: bool = False


# ---------------------------------------------------------------------------
# 编译输入：live-bind 的知识源 + 本表补齐的中文等义扩展
# ---------------------------------------------------------------------------

# ACT 六过程中带 typical_phrases 的五个：process -> (维度, belief key, 中文等义)。
# values 过程无典型语句（结构是 typical_values），不设词锚。
_ACT_MECHANISMS: dict[str, tuple[str, str, tuple[str, ...]]] = {
    "acceptance": (
        "D3",
        "control_struggle",
        (
            "我不想再这样焦虑下去了",
            "得先把焦虑彻底摆脱，我才能往前走",
            "要是我不担心了，可能就会出事",
        ),
    ),
    "cognitive_defusion": (
        "D3",
        "cognitive_fusion",
        (
            "我的念头说我没用，那我就是真的没用",
            "脑子里一直说我会失败，所以我别去试了",
            "这个念头反复出现，说明真的有危险",
        ),
    ),
    "present_moment": (
        "D3",
        "rumination_loop",
        (
            "我一直在回放到底哪里做错了",
            "万一再发生一次怎么办",
            "我没法享受任何事，因为总在担心以后",
        ),
    ),
    "self_as_context": (
        "D3",
        "fused_self_concept",
        (
            "我就是个失败者",
            "我一文不值",
            "我一直都这样，以后也不会变",
        ),
    ),
    "committed_action": (
        "D5",
        "waiting_for_readiness",
        (
            "等我自信了我就去做",
            "我得先把情绪都处理好才行",
            "我还没准备好",
        ),
    ),
}

# 社交安全行为清单：源文本以行文列举（foundations:social_anxiety_basics），
# 无法 live-bind，双语均为本表编写；en 词形须逐字出现在源文本中（单测钉住）。
_SAFETY_BEHAVIORS: tuple[tuple[str, str], ...] = (
    ("over-rehearsing", "提前把要说的话在脑子里排练了好多遍"),
    ("speaking very little", "能不说话就不说话"),
    ("avoiding eye contact", "不敢看对方的眼睛"),
    ("checking for signs of disapproval", "反复留意对方是不是不耐烦了"),
    ("leaving early", "找个借口提前离场"),
)

# MI change talk 七类：en 标记须逐字出现在类型描述原文中（单测钉住）；
# zh 为等义扩展。key 小写化，belief 侧统一小写。
_CHANGE_TALK: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "DESIRE": (
        ("I want", "I wish", "I would like"),
        ("我想", "我希望", "真希望"),
    ),
    "ABILITY": (
        ("I could", "I am able to", "I can"),
        ("我可以", "我能够", "也许能试试"),
    ),
    "REASON": (
        ("because", "the reason is"),
        ("因为", "原因是", "再这样下去会错过"),
    ),
    "NEED": (
        ("I need to", "I must", "I have to"),
        ("我需要", "我必须", "不能再这样下去了"),
    ),
    "COMMITMENT": (
        ("I will", "I intend to", "I plan to"),
        ("我会", "我打算", "明天开始"),
    ),
    "ACTIVATION": (
        ("I am ready", "I am willing"),
        ("我准备好了", "我愿意试试"),
    ),
    "TAKING_STEPS": (
        ("I have", "I did"),
        ("我已经", "我做了", "这周我每天都在"),
    ),
}

# sustain talk：源文本是概念行文，无枚举标记，双语均为本表编写。
_SUSTAIN_TALK_EN: tuple[str, ...] = (
    "I always end up back where I started",
    "it's no use",
    "I've tried before",
    "this is just how I am",
)
_SUSTAIN_TALK_ZH: tuple[str, ...] = (
    "试过没用",
    "我就这样了",
    "改不了的",
    "试了又回到原样",
)

# 认知歪曲：en 例句 live-bind；中文仅补一条代表性例句（识别参考，
# identify_only——只许当轮用，禁止沉淀为信念）。
_DISTORTION_ZH: dict[str, str] = {
    "all_or_nothing": "要么做到最好，要么干脆别做",
    "catastrophizing": "这次搞砸了，我的人生就完了",
    "mind_reading": "他们肯定觉得我很差劲",
    "fortune_telling": "我知道自己一定会出丑",
    "emotional_reasoning": "我觉得自己没用，所以我就是没用",
    "should_statements": "我应该永远都保持高效",
    "labeling": "我就是个废物",
    "mental_filter": "一整天全毁就因为那一件事",
    "disqualifying_positives": "那不算什么，换谁都能做到",
    "overgeneralization": "这次失败了，我总是失败",
    "personalization": "他不开心，肯定是因为我",
}


def _build_registry() -> tuple[ProfileAnchor, ...]:
    anchors: list[ProfileAnchor] = []

    for process, (dimension, key, zh) in _ACT_MECHANISMS.items():
        anchors.append(
            ProfileAnchor(
                anchor_id=f"{dimension}.{key}",
                dimension=dimension,
                key=key,
                en_phrases=tuple(ACT_CORE_PROCESSES[process]["typical_phrases"]),
                zh_phrases=zh,
                source=f"act:{process}:typical_phrases",
            )
        )

    anchors.append(
        ProfileAnchor(
            anchor_id="D3.avoidance_maintenance.social",
            dimension="D3",
            key="avoidance_maintenance.social",
            en_phrases=tuple(en for en, _ in _SAFETY_BEHAVIORS),
            zh_phrases=tuple(zh for _, zh in _SAFETY_BEHAVIORS),
            source="foundations:social_anxiety_basics",
        )
    )

    for type_name, (markers, zh) in _CHANGE_TALK.items():
        key = f"change_talk.{type_name.lower()}"
        anchors.append(
            ProfileAnchor(
                anchor_id=f"D5.{key}",
                dimension="D5",
                key=key,
                en_phrases=markers,
                zh_phrases=zh,
                source=f"mi:change_talk:{type_name}",
            )
        )

    anchors.append(
        ProfileAnchor(
            anchor_id="D5.sustain_talk",
            dimension="D5",
            key="sustain_talk",
            en_phrases=_SUSTAIN_TALK_EN,
            zh_phrases=_SUSTAIN_TALK_ZH,
            source="mi:change_talk:sustain_talk",
        )
    )

    for distortion_id, data in COGNITIVE_DISTORTIONS.items():
        anchors.append(
            ProfileAnchor(
                anchor_id=f"D3.distortion.{distortion_id}",
                dimension="D3",
                key=f"distortion.{distortion_id}",
                en_phrases=tuple(data["examples"]),
                zh_phrases=(_DISTORTION_ZH[distortion_id],),
                source=f"cbt:COGNITIVE_DISTORTIONS:{distortion_id}",
                identify_only=True,
            )
        )

    anchors.append(
        ProfileAnchor(
            anchor_id="D2.worry_uncontrollable",
            dimension="D2",
            key="worry_uncontrollable",
            en_phrases=(
                "not being able to stop or control worrying",
                "I can't control my worry",
                "my worrying runs away with me",
            ),
            zh_phrases=(
                # GAD-7 条目 2 原文 live-bind（单测钉住索引位），后接等义扩展。
                QUESTIONNAIRES["gad7"]["items"][1],
                "控制不住担心",
                "担心起来停不下来",
                "越想越怕",
            ),
            source="questionnaires:gad7:items[1]",
        )
    )

    return tuple(anchors)


PROFILE_ANCHORS: tuple[ProfileAnchor, ...] = _build_registry()

# D1 统一导入面：主题词表以知识库为唯一事实源，此处只 re-export。
__all__ = [
    "ANCHOR_DIMENSIONS",
    "PROFILE_ANCHORS",
    "TOPIC_KEYWORDS",
    "ProfileAnchor",
    "all_anchors",
    "anchor_by_id",
    "anchors_for_dimension",
]

_PROFILE_ANCHOR_INDEX: dict[str, ProfileAnchor] = {a.anchor_id: a for a in PROFILE_ANCHORS}


def all_anchors() -> tuple[ProfileAnchor, ...]:
    return PROFILE_ANCHORS


def anchor_by_id(anchor_id: str) -> ProfileAnchor | None:
    return _PROFILE_ANCHOR_INDEX.get(anchor_id)


def anchors_for_dimension(dimension: str) -> list[ProfileAnchor]:
    return [a for a in PROFILE_ANCHORS if a.dimension == dimension]
