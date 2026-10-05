"""画像面板数据服务（K3b）：拟人形象 + 友善分类点。

P3 决策的 API 侧实现：
- 展示词典是唯一渲染通道（术语不出存储层），查无 label 的 key 整条跳过；
- 面板默认展示集：D1（全部展示——L4 弱信号已在渲染水位外）/ D4 / D6 /
  D7 / D8；D2 严重度轴默认不出面板；D3 未经用户认领（source !=
  user_confirmed）不出——与 prompt 渲染器同一套门控，两处共用规则。
- L4 待验证假设一律不出面板：质询发生在对话里，面板只呈现已成立的了解。
- 拟人形象边界：形象是"画像有了一张脸"，返回 familiarity（已知维度数）
  供前端决定形象的"熟悉程度"表现，不扮演有独立心智的角色。
"""

from __future__ import annotations

import json

from sqlalchemy.orm import Session

from psych_support_bot.ai.profile.display_dict import (
    DIMENSIONS,
    dimension_header,
    friendly_label,
)
from psych_support_bot.ai.profile.renderer import L4_RENDER_THRESHOLD
from psych_support_bot.ai.tools.exercises import get_exercise_by_tag
from psych_support_bot.infra.db.profile_repositories import (
    has_profile_memory_data,
    is_profile_beta_accepted,
    is_profile_memory_enabled,
    is_sensitive_background_enabled,
    list_active_beliefs,
    list_rejected_beliefs,
)

# 面板默认展示的维度（P3：D2 默认隐藏，D6 暂无数据源，进入即展示）。
# D3 的"未认领不出"门控在条目级 _item_for（source == user_confirmed 才出）。
_PANEL_DIMENSIONS = ("D1", "D3", "D4", "D5", "D6", "D7")
_MAX_D8_PANEL_ITEMS = 5


def _is_en(language: str) -> bool:
    return language.strip().lower() == "en"


def _item_for(belief, language: str) -> dict | None:
    """单条 belief → 面板条目；None = 该条不出面板（逐条裁决）。"""
    if belief.dimension == "D3" and belief.source != "user_confirmed":
        return None
    if belief.layer == "L4" and (belief.dimension not in ("D1", "D4") or belief.confidence < L4_RENDER_THRESHOLD):
        # L4 的面板豁免：D1 主题（≥0.55 ≈ 两次跨轮证据，"反复出现的话题"
        # 值得让用户看见）与 D4 效果反馈（调参 C：用户亲口 worked/aversive
        # 单次即 0.55——直接反馈是最有权的信号）。机制/动机类 L4 仍不出面板。
        return None
    label = friendly_label(belief.key, language)
    if belief.dimension == "D4":
        exercise = get_exercise_by_tag(belief.key, language="en" if _is_en(language) else "zh")
        name = str(exercise.get("name")) if exercise and exercise.get("name") else None
        try:
            effect = str(json.loads(belief.value_json or "{}").get("effect") or "")
        except (TypeError, ValueError):
            effect = ""
        effect_label = friendly_label(effect, language) if effect else None
        if not name or not effect_label:
            return None
        return {"key": belief.key, "label": name, "detail": effect_label}
    if not label:
        return None
    return {"key": belief.key, "label": label, "detail": ""}


def build_profile_panel(session: Session, user_id: str, *, language: str = "zh") -> dict:
    """「画像」面板数据：拟人形象状态 + 友善分类点（已过滤敏感维度）。"""
    enabled = is_profile_memory_enabled(session, user_id)
    sections: list[dict] = []
    seen_dimensions: set[str] = set()

    for dimension in _PANEL_DIMENSIONS:
        header = dimension_header(dimension, language)
        items: list[dict] = []
        for belief in list_active_beliefs(session, user_id, dimensions=(dimension,)):
            item = _item_for(belief, language)
            if item:
                items.append(item)
        if header and items:
            seen_dimensions.add(dimension)
            sections.append({"dimension": dimension, "header": header, "items": items})

    # D8 负记忆：被搁置的话题以"边界"而非"内容"呈现。
    header = dimension_header("D8", language)
    boundary_labels = [
        label
        for label in (
            friendly_label(b.key, language) for b in list_rejected_beliefs(session, user_id, limit=_MAX_D8_PANEL_ITEMS)
        )
        if label
    ]
    if header and boundary_labels:
        seen_dimensions.add("D8")
        sections.append(
            {
                "dimension": "D8",
                "header": header,
                "items": [{"key": "boundary.avoided_topics", "label": "、".join(boundary_labels), "detail": ""}],
            }
        )

    # 未知维度防御：DIMENSIONS 之外的 section 不该存在（闭集一致性）。
    sections = [s for s in sections if s["dimension"] in DIMENSIONS]

    # 画像关闭时只返回开关状态和 has_data，不返回推断内容。
    if not enabled:
        return {
            "profile_memory": {
                "enabled": False,
                "has_data": has_profile_memory_data(session, user_id),
            },
            "avatar": {"familiarity": 0, "known_dimensions": []},
            "sections": [],
        }

    # K3 结构性理解：how_to_be_with_them + open_questions。
    understanding_section = _build_understanding_section(session, user_id, language)
    if understanding_section:
        sections.insert(0, understanding_section)

    # 身份背景：从 UserProfile.background_json 读取。
    background_section = _build_background_section(session, user_id, language)
    if background_section:
        sections.insert(0, background_section)

    return {
        "profile_memory": {
            "enabled": enabled,
            "has_data": has_profile_memory_data(session, user_id),
            "beta_accepted": is_profile_beta_accepted(session, user_id),
            "sensitive_background_enabled": is_sensitive_background_enabled(session, user_id),
        },
        "avatar": {
            # 拟人形象边界：只表达"系统对你的了解程度"，不做人格化演绎。
            "familiarity": len(seen_dimensions)
            + (1 if understanding_section else 0)
            + (1 if background_section else 0),
            "known_dimensions": sorted(seen_dimensions),
        },
        "sections": sections,
    }


def _build_understanding_section(session: Session, user_id: str, language: str) -> dict | None:
    """K3 结构性理解 → 面板 section。"""
    from psych_support_bot.infra.db.repositories import get_user_profile

    profile = get_user_profile(session, user_id)
    if not profile or not profile.understanding_json or profile.understanding_json == "{}":
        return None
    try:
        understanding = json.loads(profile.understanding_json)
    except (TypeError, ValueError):
        return None

    patterns = understanding.get("patterns", [])
    how_to = understanding.get("how_to_be_with_them", "")

    if not patterns and not how_to:
        return None

    is_en = _is_en(language)
    header = "对你整体的理解" if not is_en else "Overall understanding"
    items: list[dict] = []

    if how_to:
        items.append({"key": "how_to_be", "label": how_to, "detail": ""})

    for p in patterns[:3]:
        if p.get("needs_verification"):
            continue  # 面板只展示确定的，待验证的在对话中悄悄问
        desc = p.get("description", "")
        if desc:
            items.append({"key": f"pattern_{desc[:20]}", "label": desc, "detail": ""})

    # open_questions 不展示——它们是系统内部的探索方向，不是给用户看的。

    return {"dimension": "K3", "header": header, "items": items} if items else None


def _build_background_section(session: Session, user_id: str, language: str) -> dict | None:
    """身份背景 → 面板 section。"""
    from psych_support_bot.infra.db.repositories import get_user_profile

    profile = get_user_profile(session, user_id)
    if not profile or not profile.background_json or profile.background_json == "{}":
        return None
    try:
        bg = json.loads(profile.background_json)
    except (TypeError, ValueError):
        return None

    if not bg:
        return None

    is_en = _is_en(language)
    header = "背景信息" if not is_en else "Background"
    # 友善化键名映射。
    # religion 不在前端展示（使用代号存储，此处不映射）。
    # medical 仅在用户开启敏感背景授权时展示。
    sensitive_bg = is_sensitive_background_enabled(session, user_id)
    key_labels = {
        "occupation": ("职业", "Occupation"),
        "age": ("年龄", "Age"),
        "family": ("家庭", "Family"),
        "living": ("居住", "Living situation"),
        "cultural": ("文化背景", "Cultural background"),
        "support_network": ("支持网络", "Support network"),
    }
    if sensitive_bg:
        key_labels["medical"] = ("健康", "Health")
    items: list[dict] = []
    for key, (zh_label, en_label) in key_labels.items():
        val = bg.get(key)
        if val:
            label = en_label if is_en else zh_label
            items.append({"key": f"bg_{key}", "label": label, "detail": str(val)})

    return {"dimension": "BG", "header": header, "items": items} if items else None
