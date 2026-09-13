"""画像 memory block 渲染器（K1c）。

动态字符预算（知识提炼 §6 定稿，PROFILE_RENDER_* setting）：

- ``budget = clamp(BASE × (1.2 − 0.4 × pressure), FLOOR, CAP)``
- ``pressure = 0.7 × min(tail_load / NOMINAL_TAIL_LOAD, 1) + 0.3 × knowledge_proxy``
  ——历史/摘要负载占 7 成，本轮主题命中数占 3 成。图内 LLM 语义主题在
  快照渲染之后才产生，此处用确定性 ``detect_topics`` 做先行代理，
  K2 图内重渲染时替换。
- **整条装箱，不做剪刀截断**：预算是装箱容量，任何条目绝不截断成残句。
- 唯一例外是 D8 固定首槽：负记忆无条件渲染（FLOOR 语义），但
  MAX_D8_ITEMS 上限保证其自身有界。
- 确定性：渲染是本轮可观测量的确定性函数，同输入同输出。

维度渲染规则（PROFILE_DECISIONS P3 的渲染侧实现）：
- D3 机制信念未经用户确认（source != user_confirmed）**不出现在任何渲染**；
- L4 候选仅 confidence ≥ L4_RENDER_THRESHOLD（≈2 次跨轮证据）才渲染；
- 任何条目 label 查词典查无（friendly_label 返回 None）即整条跳过——
  把存储层 key 透出到 prompt 等于术语泄漏。

排序规则（信息增益调度）：
- 条目按活性（``belief_activity``）装箱：置信度 × 时间衰减（60 天半衰期）
  × 确认强度（L1/L2=1.0，L4=0.7）。活性只影响排序，**不影响门控**——
  旧信念不会因衰减被隐藏，只是在预算竞争中自然下沉。
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from psych_support_bot.ai.knowledge.index import detect_topics
from psych_support_bot.ai.profile.display_dict import friendly_label
from psych_support_bot.ai.tools.exercises import get_exercise_by_tag
from psych_support_bot.infra.config.settings import get_settings
from psych_support_bot.infra.db.profile_repositories import (
    list_active_beliefs,
    list_rejected_beliefs,
)

logger = logging.getLogger(__name__)

# ── 动态预算常数（BASE/FLOOR/CAP 在 settings；调参走 eval 证据）──────────
NOMINAL_TAIL_LOAD = 1800  # 历史+摘要的名义满载字符数
BASE_MULTIPLIER_HIGH = 1.2  # pressure=0 时的放大系数
BASE_MULTIPLIER_LOW = 0.8  # pressure=1 时的收缩系数
W_TAIL = 0.7  # 历史负载权重
W_KNOWLEDGE = 0.3  # 本轮主题命中权重
MAX_KNOWLEDGE_PROXY = 3  # 主题命中数封顶（与 detect_topics 返回上限一致）
MAX_D8_ITEMS = 5  # D8 负记忆条数上限（保证固定首槽自身有界）
L4_RENDER_THRESHOLD = 0.55  # L4 渲染水位（0.4 起步 + 一次 SUPPORT_GAIN）

# ── 活性排序（信息增益调度）────────────────────────────────────────────
# 活性 = 置信度 × 时间衰减 × 确认强度，只决定装箱排序，不参与红线门控
# （L4 水位 / D3 未认领门控仍看原始字段，避免旧信念被衰减"偷偷隐藏"）。
ACTIVITY_HALF_LIFE_DAYS = 60.0  # 证据半衰期：60 天无新证据活性减半
_CONFIRMED_LAYER_ACTIVITY = 1.0  # L1/L2 用户已认领：活性不打折
_HYPOTHESIS_LAYER_ACTIVITY = 0.7  # L4 待验证假设：天然弱于已认领

_RISK_AWARE_LEVELS = {"elevated", "high", "critical"}


def belief_activity(belief, *, now: datetime | None = None, half_life_days: float = ACTIVITY_HALF_LIFE_DAYS) -> float:
    """单条 belief 的当前活性（纯函数）。

    - 时间衰减：``0.5 ** (age_days / half_life)``，last_evidence_at 是
      模型注释中"90 天降权"承诺的兑现（不删除，只降排序）；
    - 确认强度：L1/L2 系数 1.0，L4 系数 0.7——同等置信度下已认领信念
      优先于候选假设；
    - 输出 [0,1] 量级（confidence ≤0.95），同输入同输出。
    """
    current = now or datetime.now(UTC)
    last = belief.last_evidence_at
    if last is None:
        return 0.0
    if last.tzinfo is None:
        # SQLite 经 SQLAlchemy 取回的 naive datetime 按 UTC 解释。
        last = last.replace(tzinfo=UTC)
    age_days = max((current - last).total_seconds() / 86400.0, 0.0)
    decay = 0.5 ** (age_days / max(half_life_days, 1e-6))
    layer_factor = _CONFIRMED_LAYER_ACTIVITY if belief.layer in {"L1", "L2"} else _HYPOTHESIS_LAYER_ACTIVITY
    return round(float(belief.confidence) * decay * layer_factor, 6)


def _is_en(language: str) -> bool:
    return language.strip().lower() == "en"


def compute_profile_budget(
    tail_load: int,
    knowledge_proxy: float,
    *,
    base: int,
    floor: int,
    cap: int,
) -> int:
    """动态预算纯函数。knowledge_proxy ∈ [0, 1]（本轮主题命中/3）。"""
    tail_pressure = min(max(tail_load, 0) / NOMINAL_TAIL_LOAD, 1.0)
    proxy = min(max(knowledge_proxy, 0.0), 1.0)
    pressure = W_TAIL * tail_pressure + W_KNOWLEDGE * proxy
    raw = base * (BASE_MULTIPLIER_HIGH - (BASE_MULTIPLIER_HIGH - BASE_MULTIPLIER_LOW) * pressure)
    return int(max(floor, min(cap, round(raw))))


def _d8_line(rejected_keys: list[str], language: str) -> str | None:
    labels = [text for text in (friendly_label(key, language) for key in rejected_keys) if text]
    if not labels:
        return None
    joiner = ", " if _is_en(language) else "、"
    if _is_en(language):
        return f"Avoid (user has set these aside): {joiner.join(labels)}"
    return f"应回避（用户已明确搁置）：{joiner.join(labels)}"


def _belief_item(belief, language: str) -> str | None:
    """单条 belief → 电报体短语；返回 None = 该条不渲染（逐条裁决）。"""
    # P3：机制信念未经用户认领，不进任何渲染（面板与 prompt 同规）。
    if belief.dimension == "D3" and belief.source != "user_confirmed":
        return None
    # L4 候选须过渲染水位：单次弱信号不进 prompt（防一次性误读自我强化）。
    if belief.layer == "L4" and belief.confidence < L4_RENDER_THRESHOLD:
        return None

    label = friendly_label(belief.key, language)
    if belief.dimension == "D1":
        # 主题 key 无标签即跳过：绝不把裸 key 透进 prompt。
        if not label:
            return None
        return f"{label} ({'recurring topic'})" if _is_en(language) else f"{label}（反复出现的话题）"

    if belief.dimension == "D4":
        # 练习名走 exercises 库既有展示名，效果值走词典；任一缺省即跳过。
        exercise = get_exercise_by_tag(belief.key, language="en" if _is_en(language) else "zh")
        name = str(exercise.get("name")) if exercise and exercise.get("name") else None
        try:
            effect = str(json.loads(belief.value_json or "{}").get("effect") or "")
        except (TypeError, ValueError):
            effect = ""
        effect_label = friendly_label(effect, language) if effect else None
        if not name or not effect_label:
            return None
        return f"{name} — {effect_label}" if _is_en(language) else f"{name}，{effect_label}"

    # D2/D5/D7 等锚 key：词典直出（含 protective.*，K3 数据到位即生效）。
    return label


def render_profile_block(
    session: Session,
    user_id: str,
    *,
    language: str = "",
    user_message: str = "",
    recent_risk_level: str = "",
    tail_load: int = 0,
) -> str | None:
    """渲染画像 memory block；无可渲染内容返回 None（fail-open 上游约定）。"""
    settings = get_settings()
    topics = detect_topics(user_message) if user_message else []
    budget = compute_profile_budget(
        tail_load,
        min(len(topics) / MAX_KNOWLEDGE_PROXY, 1.0),
        base=settings.profile_render_base,
        floor=settings.profile_render_floor,
        cap=settings.profile_render_cap,
    )

    is_en = _is_en(language)
    joiner = "; " if is_en else "；"
    items: list[str] = []

    # D8 固定首槽：负记忆无条件渲染，不参与装箱竞争。
    d8 = _d8_line([b.key for b in list_rejected_beliefs(session, user_id, limit=MAX_D8_ITEMS)], language)
    if d8:
        items.append(d8)

    risk_boost = recent_risk_level in _RISK_AWARE_LEVELS
    render_now = datetime.now(UTC)

    def _sort_key(belief):
        topic_match = 1 if belief.dimension == "D1" and belief.key in topics else 0
        d7_boost = 1 if risk_boost and belief.dimension == "D7" else 0
        # 活性取代旧的（layer_bonus, last_evidence_at）二元组：置信度 ×
        # 时间衰减 × 确认强度的连续排序，旧信念自然下沉；时近仍作同分时的
        # 确定性末位 tiebreak，保证渲染可复现。
        return (topic_match, d7_boost, belief_activity(belief, now=render_now), belief.last_evidence_at)

    candidates = sorted(list_active_beliefs(session, user_id), key=_sort_key, reverse=True)
    for belief in candidates:
        text = _belief_item(belief, language)
        if text:
            items.append(text)

    # 整条装箱：D8 首槽豁免，其余条目放不下整条就停，绝不截断。
    rendered: list[str] = []
    used = 0
    dropped = 0
    for index, text in enumerate(items):
        prefix_cost = len(joiner) if rendered else 0
        exempt = index == 0 and d8 is not None
        if not exempt and used + prefix_cost + len(text) > budget:
            dropped += 1
            continue
        used += prefix_cost + len(text)
        rendered.append(text)

    block = joiner.join(rendered)
    logger.info(
        "profile render: chars=%d budget=%d items=%d dropped=%d user=%s",
        used,
        budget,
        len(rendered),
        dropped,
        user_id,
    )
    return block or None
