"""画像展示词典——P3 决策下术语出不了存储层的唯一渲染通道。

决策依据（docs/technical/PROFILE_DECISIONS.md P3）：内部 belief 存临床中性
结构化语言（dimension D1-D8 + belief key），用户界面只呈现"拟人形象 + 友善
分类画像点"，**不使用心理学术语**。本词典是唯一映射通道——任何把 claim
原文或 belief key 直接透出面板的做法都是违规。

硬红线：
- 所有用户可见字符串禁用临床词汇（抑郁症/焦虑症/人格/认知融合/反刍/
  灾难化……单测以黑名单钉住）。
- ``distortion.*`` 命名空间（identify_only，仅当轮干预识别）**永不展示**，
  绝不进入本词典（单测钉住）。
- D3 机制信念在用户经质询亲口认领之前不上面板；词典提前备好标签不改变
  这个门控——门控在渲染调用方，本模块只管"认领之后怎么说话"。
- 措辞必须是观察式（"压力来的时候，你好像更想先躲一躲"），禁止身份判定
  式（"你是回避型"）；归属始终是"系统对你观察的整理"，不替用户下结论。

键命名空间（与 belief key / TOPIC_KEYWORDS 对齐，测试交叉钉住）：
- D1：TOPIC_KEYWORDS 的 20 个主题 key 原样入典。
- D2/D3/D5：anchors.py 的非 distortion 锚 key 原样入典。
- D4：练习效果三值 ``worked`` / ``neutral`` / ``aversive``（练习名称本身
  走 exercises 库既有展示名，不入本典）。
- D7：``protective.*`` 六组，语义对应安全计划六节（步骤 1-6 顺序一致）。
- D8：``boundary.avoided_topics`` 负记忆分组。

数据为模块级 frozen 结构，无 I/O。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

# 面板分区维度闭集。D6（沟通偏好）暂无可展示 key，但分区标题先备齐，
# 面板骨架不因维度渐进落地而改版。
DIMENSIONS: tuple[str, ...] = ("D1", "D2", "D3", "D4", "D5", "D6", "D7", "D8")


@dataclass(frozen=True)
class DisplayLabel:
    """单条展示标签：key 定位画像槽位，双语友善措辞供面板渲染。"""

    key: str
    dimension: str
    zh: str
    en: str


# ---------------------------------------------------------------------------
# D1 关注主题：TOPIC_KEYWORDS 20 个主题 key 的生活化标签。
# 措辞原则：说"处境与感受"，不说病名；relaxation 是意图不是困扰，措辞相应。
# ---------------------------------------------------------------------------
_TOPIC_LABELS: dict[str, tuple[str, str]] = {
    "anxiety": ("容易紧张担心", "Often worried or on edge"),
    "panic": ("突然涌上来的心慌和害怕", "Sudden rushes of intense fear"),
    "depression": ("情绪低落、提不起劲", "Feeling down or drained"),
    "sleep": ("睡不好、睡不安稳", "Trouble sleeping"),
    "ocd": ("反复出现、停不下来的念头或举动", "Thoughts or urges that keep coming back and are hard to stop"),
    "burnout": ("长期透支、累垮了的感觉", "Worn out and running on empty"),
    "grief": ("失去重要的人或事之后的难过", "Coping with losing someone or something important"),
    "anger": ("容易上火、压不住脾气", "Feeling angry or quick to snap"),
    "procrastination": ("想开始却总是开始不了的事", "Trouble getting started on things"),
    "rumination": ("在脑子里一遍遍停不下来地想", "Stuck thinking the same things over and over"),
    "self_worth": ("常常觉得自己不够好", "Often feeling not good enough"),
    "relationships": ("关系里的烦恼和孤单", "Relationship ups and downs, and feeling lonely"),
    "stress": ("压力太大、一直紧绷着", "Feeling stressed and stretched thin"),
    "motivation": ("打不起精神、什么都不想做", "Finding it hard to get moving"),
    "social_anxiety": ("怕被评价、怕在人前出丑", "Worry about being judged around others"),
    "ptsd": ("过去难受的经历还会突然冒出来", "Past hard experiences that still resurface"),
    "eating_disorder": ("和吃东西之间的拉扯", "A strained relationship with food"),
    "nssi": ("心里太难受时弄伤自己的时刻", "Moments of hurting yourself to cope"),
    "substance_use": ("想靠喝酒或其他东西撑过去", "Leaning on alcohol or other substances to get by"),
    "relaxation": ("想让自己缓缓、平静下来", "Wanting to calm down and unwind"),
}

# ---------------------------------------------------------------------------
# D2/D3/D5 机制与动机锚 key：观察式措辞，"你好像……"是标准句式。
# ---------------------------------------------------------------------------
_ANCHOR_LABELS: dict[str, dict[str, tuple[str, str]]] = {
    "D2": {
        "worry_uncontrollable": ("担心一上来，就好像有点停不下来", "Once worry starts, it feels hard to switch off"),
    },
    "D3": {
        "control_struggle": (
            "难受的感觉来时，你好像总想先把它赶走",
            "When something feels bad, you seem to want it gone right away",
        ),
        "cognitive_fusion": (
            "有些念头一冒出来，对你来说就好像特别真",
            "Some thoughts can feel very real the moment they show up",
        ),
        "rumination_loop": (
            "有些事你好像会在脑子里一遍遍回放",
            "Some things seem to replay in your mind over and over",
        ),
        "fused_self_concept": (
            "不顺心的时候，你好像容易觉得'我就是这样的人'",
            "When things go wrong, it can feel like 'this is just who I am'",
        ),
        "avoidance_maintenance.social": (
            "要见人或露面的场合，你好像常常想先躲一躲",
            "In social situations, you often seem to want to step back first",
        ),
    },
    "D5": {
        "waiting_for_readiness": ("你在等一个'完全准备好'的时刻", "Waiting until you feel fully ready"),
        "change_talk.desire": ("你想改变的念头在冒头", "You want things to change"),
        "change_talk.ability": ("你开始觉得自己有可能做得到", "You're starting to believe you can"),
        "change_talk.reason": ("你说得出为什么要改变", "You have your reasons to change"),
        "change_talk.need": ("你越来越觉得'不能再这样下去了'", "You're feeling that things can't go on like this"),
        "change_talk.commitment": ("你说出了具体的打算", "You've said what you plan to do"),
        "change_talk.activation": ("你准备好迈出第一步了", "You're ready to take a first step"),
        "change_talk.taking_steps": ("你已经在做一些改变了", "You're already taking steps"),
        "sustain_talk": (
            "你心里既有想变的部分，也有拿不准的部分",
            "Part of you wants change, part of you feels unsure",
        ),
    },
}

# ---------------------------------------------------------------------------
# D4 干预响应：练习效果三值（练习名称走 exercises 库既有展示名）。
# ---------------------------------------------------------------------------
_EFFECT_LABELS: dict[str, tuple[str, str]] = {
    "worked": ("用起来有帮助", "Helpful when you used it"),
    "neutral": ("感觉一般", "So-so"),
    "aversive": ("用着不太合适", "Didn't feel right for you"),
}

# ---------------------------------------------------------------------------
# D7 保护因子：语义对应安全计划六节（SAFETY_PLAN_TEMPLATE.sections 步骤
# 1-6 顺序一致，单测钉住数量）；措辞全部转成"支撑与资源"的友善口径。
# ---------------------------------------------------------------------------
_PROTECTIVE_LABELS: dict[str, tuple[str, str]] = {
    "protective.warning_signs": ("需要留意的信号", "Signs to watch for"),
    "protective.coping_strategies": ("自己就能做的安抚方法", "Ways to soothe yourself"),
    "protective.support_people": ("可以开口求助的人", "People you can turn to"),
    "protective.professional_contacts": ("专业支持的联系方式", "Professional support contacts"),
    "protective.environment_safety": ("让身边环境更安心的办法", "Ways to make your space feel safer"),
    "protective.reasons_for_living": ("支撑你走下去的理由", "What keeps you going"),
}

# ---------------------------------------------------------------------------
# D8 负记忆分组：渲染优先级最高（设计文档 §2 D8），措辞尊重用户边界。
# ---------------------------------------------------------------------------
_BOUNDARY_LABELS: dict[str, tuple[str, str]] = {
    "boundary.avoided_topics": ("你说过暂时不想聊的话题", "Topics you've asked to set aside for now"),
}

# ---------------------------------------------------------------------------
# 维度分区标题（面板分区用）。
# ---------------------------------------------------------------------------
_DIMENSION_HEADERS: dict[str, tuple[str, str]] = {
    "D1": ("最近在关注", "On your mind lately"),
    "D2": ("最近的整体状态", "How you've been doing"),
    "D3": ("一起留意到的模式", "Patterns we've noticed together"),
    "D4": ("对你有帮助的", "What's helped you"),
    "D5": ("你想去的方向", "Where you'd like to head"),
    "D6": ("你偏好的相处方式", "How you like to work together"),
    "D7": ("你的底气与支撑", "Your sources of strength"),
    "D8": ("暂时搁置的话题", "Set aside for now"),
}


def _build_labels() -> tuple[DisplayLabel, ...]:
    labels: list[DisplayLabel] = []
    labels.extend(DisplayLabel(key, "D1", zh, en) for key, (zh, en) in _TOPIC_LABELS.items())
    for dimension, keyed in _ANCHOR_LABELS.items():
        labels.extend(DisplayLabel(key, dimension, zh, en) for key, (zh, en) in keyed.items())
    labels.extend(DisplayLabel(key, "D4", zh, en) for key, (zh, en) in _EFFECT_LABELS.items())
    labels.extend(DisplayLabel(key, "D7", zh, en) for key, (zh, en) in _PROTECTIVE_LABELS.items())
    labels.extend(DisplayLabel(key, "D8", zh, en) for key, (zh, en) in _BOUNDARY_LABELS.items())
    return tuple(labels)


ALL_LABELS: tuple[DisplayLabel, ...] = _build_labels()

_LABEL_INDEX: dict[str, DisplayLabel] = {label.key: label for label in ALL_LABELS}

# 只读暴露：面板与测试可直接断言 "key 是否可展示"（distortion.* 必须查无）。
DISPLAY_LABELS: Mapping[str, DisplayLabel] = MappingProxyType(_LABEL_INDEX)
DIMENSION_HEADERS: Mapping[str, tuple[str, str]] = MappingProxyType(_DIMENSION_HEADERS)


def _is_en(language: str) -> bool:
    # 与 memory_modules._is_en 同口径：仅 "en" 视为英文，其余（含空/未知）
    # 一律回退中文——中文是默认展示语言。
    return language.strip().lower() == "en"


def friendly_label(key: str, language: str = "zh") -> str | None:
    """belief key / 主题 key / 效果值 -> 用户可见的友善措辞。

    查无此 key 返回 None（调用方应跳过该条，而不是回退显示 key 本身——
    把 key 透出面板等于把存储层术语漏给用户）。``language`` 仅 ``en`` 渲染
    英文，其余一律中文。
    """
    label = _LABEL_INDEX.get(key)
    if label is None:
        return None
    return label.en if _is_en(language) else label.zh


def dimension_header(dimension: str, language: str = "zh") -> str | None:
    """维度 D1-D8 -> 面板分区标题；闭集之外返回 None。"""
    if not dimension:
        return None
    header = _DIMENSION_HEADERS.get(dimension.strip().upper())
    if header is None:
        return None
    return header[1] if _is_en(language) else header[0]
