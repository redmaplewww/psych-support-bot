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

检索规则（加权打分 + 多样性，借鉴知识库检索机制）：
- 每条信念经 ``score_belief`` 多信号加权打分：主题命中(+5)、风险感知(+4)、
  用户确认(+3)、活性(+2)、多证据(+1)、时新性(+1)。
- 零分信念直接过滤（不相关），不再全量渲染后裁剪。
- 多样性限制：每维度最多 _MAX_PER_DIMENSION 条，防止单维度占满槽位。
- 活性（置信度 × 60天半衰衰减 × 确认强度）只影响排序，不影响门控——
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

from psych_support_bot.ai.profile.constants import (
    ACTIVITY_HALF_LIFE_DAYS,
    CURIOSITY_SIGNAL_THRESHOLD,
    D5_POSITION_THRESHOLD,
    D6_TONE_THRESHOLD,
    L4_RENDER_THRESHOLD,
    LOW_CONFIDENCE_PATTERN_CEIL,
    LOW_CONFIDENCE_PATTERN_FLOOR,
    MAX_D8_ITEMS,
    MAX_LIFE_EVENTS,
    TIME_LABEL_ACTIVE_DAYS,
    TIME_LABEL_MONTHLY_DAYS,
    TIME_LABEL_WEEKLY_DAYS,
)

# ── 活性排序（信息增益调度）────────────────────────────────────────────
# 活性 = 置信度 × 时间衰减 × 确认强度，只决定装箱排序，不参与红线门控
# （L4 水位 / D3 未认领门控仍看原始字段，避免旧信念被衰减"偷偷隐藏"）。
_CONFIRMED_LAYER_ACTIVITY = 1.0  # L1/L2 用户已认领：活性不打折
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


# ── 加权打分（借鉴知识库 retrieve_knowledge_entries） ─────────────────

# 每个维度最多渲染条目数（多样性防止单维度占满槽位）。
_MAX_PER_DIMENSION = 3

_SCORE_TOPIC_HIT = 5.0  # D1 key 命中当前消息主题
_SCORE_RISK_D7_BOOST = 4.0  # 风险升高时 D7 保护因素加权
_SCORE_LAYER_CONFIRMED = 3.0  # L1/L2 用户确认的信念
_SCORE_ACTIVITY_WEIGHT = 2.0  # 活性分数权重（置信度×衰减×确认强度）
_SCORE_EVIDENCE_BONUS = 1.0  # 多证据支持（evidence_count >= 3）
_SCORE_FRESHNESS = 1.0  # 最近一轮有新证据


def score_belief(
    belief,
    *,
    topics: set[str],
    risk_boost: bool,
    now: datetime | None = None,
) -> float:
    """多信号加权打分（知识库风格）。返回 >= 0 的分数，0 = 不相关。"""
    score = 0.0

    # Topic hit：D1 主题信念的 key 命中当前消息主题。
    if belief.dimension == "D1" and belief.key in topics:
        score += _SCORE_TOPIC_HIT

    # 风险感知：D7 保护因素在危机/高风险时加权。
    if risk_boost and belief.dimension == "D7":
        score += _SCORE_RISK_D7_BOOST

    # 确认层级：用户明确确认的信念比自动提取的更重要。
    if belief.layer in ("L1", "L2"):
        score += _SCORE_LAYER_CONFIRMED

    # 活性分数（置信度 × 时间衰减 × 确认强度）。
    activity = belief_activity(belief, now=now)
    score += activity * _SCORE_ACTIVITY_WEIGHT

    # 多证据支持：跨轮多次确认的信念更可靠。
    try:
        val = json.loads(belief.value_json or "{}") or {}
        evidence_count = len(val.get("evidence_ids") or [])
        if evidence_count >= 3:
            score += _SCORE_EVIDENCE_BONUS
    except (TypeError, ValueError):
        pass

    # 时新性：最近一轮有新证据的信念加权。
    if now and belief.last_evidence_at:
        last = belief.last_evidence_at
        if last.tzinfo is None:
            last = last.replace(tzinfo=UTC)
        hours_since = (now - last).total_seconds() / 3600
        if hours_since < 2:
            score += _SCORE_FRESHNESS

    return round(score, 4)


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


def _time_ago_label(belief, language: str = "") -> str:
    """紧凑时间标注：基于 last_evidence_at 输出"3天前"/"2周前"等。

    7天内输出"活跃"（高频信号无需精确天数）；超过90天不标注（已衰减到
    低优先级，标注反而占预算）。返回空串表示不标注。
    """
    ts = getattr(belief, "last_evidence_at", None)
    if ts is None:
        return ""
    now = datetime.now(UTC)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    days = max(0, (now - ts).days)
    if days > TIME_LABEL_MONTHLY_DAYS:
        return ""
    is_en = _is_en(language)
    if days <= TIME_LABEL_ACTIVE_DAYS:
        return "active" if is_en else "活跃"
    if days <= TIME_LABEL_WEEKLY_DAYS:
        weeks = max(1, days // 7)
        return f"{weeks}w ago" if is_en else f"{weeks}周前"
    months = max(1, days // 30)
    return f"{months}mo ago" if is_en else f"{months}月前"


def _context_tags_suffix(belief, language: str = "") -> str:
    """从 value_json 提取 context_tags，输出紧凑后缀（"·工作·关系"）。"""
    try:
        val = json.loads(belief.value_json or "{}")
    except (TypeError, ValueError):
        return ""
    tags = val.get("context_tags", [])
    if not tags:
        return ""
    return "".join(f"·{t}" for t in tags[:3])  # 最多显示3个标签


def _belief_item(belief, language: str) -> str | None:
    """单条 belief → 电报体短语；返回 None = 该条不渲染（逐条裁决）。"""
    # P3：机制信念未经用户认领，不进任何渲染（面板与 prompt 同规）。
    if belief.dimension == "D3" and belief.source != "user_confirmed":
        return None
    # L4 候选须过渲染水位：单次弱信号不进 prompt（防一次性误读自我强化）。
    if belief.layer == "L4" and belief.confidence < L4_RENDER_THRESHOLD:
        return None

    label = friendly_label(belief.key, language)
    time_tag = _time_ago_label(belief, language)

    if belief.dimension == "D1":
        # 主题 key 无标签即跳过：绝不把裸 key 透进 prompt。
        if not label:
            return None
        if time_tag:
            return f"{label}（反复话题·{time_tag}）" if not _is_en(language) else f"{label} (recurring·{time_tag})"
        return f"{label}（反复出现的话题）" if not _is_en(language) else f"{label} (recurring topic)"

    if belief.dimension == "D2" and belief.key.startswith("severity."):
        # D2 严重度信念：紧凑格式带分数、等级和时间。
        try:
            val = json.loads(belief.value_json or "{}")
        except (TypeError, ValueError):
            val = {}
        score = val.get("score")
        severity = val.get("severity", "")
        if label and score is not None:
            sev_label = (
                {
                    "minimal": "极低",
                    "none": "无",
                    "mild": "轻度",
                    "subthreshold": "亚临床",
                    "moderate": "中度",
                    "moderately_severe": "中重度",
                    "severe": "重度",
                }.get(severity, severity)
                if not _is_en(language)
                else severity
            )
            parts = [f"{label}·{sev_label}{score}分" if not _is_en(language) else f"{label}·{sev_label}{score}"]
            if time_tag:
                parts.append(time_tag)
            return "·".join(parts)
        return label

    if belief.dimension == "D3":
        # 通路3：有 cycle 且已用用户情境填充时，渲染结构化摘要。
        try:
            val = json.loads(belief.value_json or "{}")
        except (TypeError, ValueError):
            val = {}
        cycle = val.get("cycle") if isinstance(val.get("cycle"), dict) else None
        if cycle and val.get("user_specific"):
            is_en = _is_en(language)
            trigger = cycle.get("trigger", "")
            behavior = cycle.get("behavior", "")
            maintains = cycle.get("maintains", "")
            if trigger and behavior:
                if is_en:
                    return f"In {trigger}, you tend to {behavior}" + (f" — {maintains}" if maintains else "")
                return f"在{trigger}时，你倾向于{behavior}" + (f"（{maintains}）" if maintains else "")
        # 无 cycle 时走词典标签（需 user_confirmed，已在上方门控）。
        return label

    if belief.dimension == "D4":
        # 练习名走 exercises 库既有展示名，效果值走词典；任一缺省即跳过。
        exercise = get_exercise_by_tag(belief.key, language="en" if _is_en(language) else "zh")
        name = str(exercise.get("name")) if exercise and exercise.get("name") else None
        try:
            val = json.loads(belief.value_json or "{}")
        except (TypeError, ValueError):
            val = {}
        effect = str(val.get("effect") or "")
        effect_label = friendly_label(effect, language) if effect else None
        if not name or not effect_label:
            return None
        # 通路2：有 target_symptom 时渲染更丰富的信息。
        target = str(val.get("target_symptom") or "")
        target_label = friendly_label(target, language) if target else None
        if target_label and effect == "worked":
            base = f"{name}—对{target_label}有帮助" if not _is_en(language) else f"{name}—helped with {target_label}"
        elif _is_en(language):
            base = f"{name}—{effect_label}"
        else:
            base = f"{name}—{effect_label}"
        if time_tag:
            return f"{base}·{time_tag}"
        return base

    # D5 目标：从 value_json 读取目标文本，不依赖词典。
    if belief.dimension == "D5" and belief.key.startswith("goal."):
        try:
            val = json.loads(belief.value_json or "{}")
        except (TypeError, ValueError):
            val = {}
        goal_text = str(val.get("goal") or "")
        if not goal_text:
            return None
        base = f"Goal: {goal_text}" if _is_en(language) else f"目标：{goal_text}"
        if time_tag:
            return f"{base}·{time_tag}"
        return base

    # D2/D5/D7 等锚 key：词典直出（含 protective.*，K3 数据到位即生效）。
    if not label:
        return None
    ctx = _context_tags_suffix(belief, language)
    return f"{label}{ctx}" if ctx else label


# ── MI 改变谈话梯度 → D5 位置摘要 ──────────────────────────────────────
_MI_GRADIENT: dict[str, int] = {
    "change_talk.desire": 1,
    "change_talk.ability": 2,
    "change_talk.reason": 3,
    "change_talk.need": 4,
    "change_talk.commitment": 5,
    "change_talk.activation": 6,
    "change_talk.taking_steps": 7,
}

_D5_POSITION_ZH: dict[str, str] = {
    "low": "你表达了想要改变的愿望，我们可以一起探索方向。",
    "mid": "你已经有了改变的理由，接下来可以看看具体的步骤。",
    "high": "你已经准备好行动了，我们可以一起制定计划。",
    "active": "你已经在行动中了，我们可以一起回顾进展。",
}
_D5_POSITION_EN: dict[str, str] = {
    "low": "You have expressed a desire to change — we can explore directions together.",
    "mid": "You have clear reasons to change — next we can look at concrete steps.",
    "high": "You are ready to act — we can make a plan together.",
    "active": "You are already taking steps — we can review your progress together.",
}


def _d5_position_summary(session: Session, user_id: str, language: str = "") -> str | None:
    """MI 改变连续谱位置摘要：取最高层级的 D5 活跃信念，输出一句话。"""
    beliefs = list_active_beliefs(session, user_id, dimensions=("D5",), limit=10)
    if not beliefs:
        return None
    best_tier = 0
    has_sustain = False
    for b in beliefs:
        if b.key == "sustain_talk" and b.confidence >= D5_POSITION_THRESHOLD:
            has_sustain = True
            continue
        tier = _MI_GRADIENT.get(b.key, 0)
        if tier > best_tier and b.confidence >= D5_POSITION_THRESHOLD:
            best_tier = tier
    if best_tier == 0:
        return None
    if best_tier <= 2:
        bucket = "low"
    elif best_tier <= 4:
        bucket = "mid"
    elif best_tier <= 6:
        bucket = "high"
    else:
        bucket = "active"
    is_en = _is_en(language)
    summary = _D5_POSITION_EN[bucket] if is_en else _D5_POSITION_ZH[bucket]
    if has_sustain:
        summary += (
            " At the same time, you have some hesitation — that is perfectly normal."
            if is_en
            else "同时你也有一些犹豫，这很正常。"
        )
    return summary


# ── D6 语气指令 ──────────────────────────────────────────────────────
_D6_TONE_DIRECTIVES: dict[str, tuple[str, str]] = {
    "prefers_brief": ("Keep your response short and direct.", "回复简短直接，不展开。"),
    "prefers_deep": ("Explore the topic in depth with the user.", "帮用户深入探索，把事情弄清楚。"),
    "dislikes_questions": ("Minimize questions; use observations and reflections instead.", "少提问，多用观察和反射。"),
    "prefers_listening": ("Prioritize reflective listening over advice-giving.", "以倾听和反射为主，不急着给建议。"),
    "prefers_action": ("Offer practical, actionable suggestions.", "给出具体可操作的建议。"),
}


def d6_tone_directives(session: Session, user_id: str, language: str = "") -> str:
    """从 D6 活跃信念中生成语气指令（追加到 loop_hint）。"""
    beliefs = list_active_beliefs(session, user_id, dimensions=("D6",), limit=5)
    if not beliefs:
        return ""
    is_en = _is_en(language)
    directives: list[str] = []
    for b in beliefs:
        if b.confidence < D6_TONE_THRESHOLD:
            continue
        zh, en = _D6_TONE_DIRECTIVES.get(b.key, ("", ""))
        directives.append(en if is_en else zh)
    return " ".join(directives)


# ── 好奇心注入：不确定时自然提问 ──────────────────────────────────────

CURIOSITY_SIGNALS_ZH = {
    "needs_clarification": "我注意到之前有些理解可能不太准确，找个合适的时候我想和你确认一下。",
    "new_topic": "这个话题你之前没怎么提过，愿意多说一点吗？",
    "low_confidence_pattern": "我隐约感觉到一些模式，但还不太确定，你愿意帮我想想看吗？",
}
CURIOSITY_SIGNALS_EN = {
    "needs_clarification": "I noticed something I'm not sure I understood correctly — I'd like to check in with you when it feels right.",
    "new_topic": "You haven't mentioned this before — would you like to share more?",
    "low_confidence_pattern": "I'm sensing a pattern but I'm not sure yet — would you help me think about it?",
}


def curiosity_signal(session: Session, user_id: str, language: str = "") -> str | None:
    """检测不确定信号，返回一个温和的好奇提问（最多一个，不重复）。"""
    beliefs = list_active_beliefs(session, user_id, limit=20)
    if not beliefs:
        return None
    is_en = _is_en(language)
    signals = CURIOSITY_SIGNALS_EN if is_en else CURIOSITY_SIGNALS_ZH

    # 1. 有 needs_clarification 标记的信念（矛盾待澄清）
    for b in beliefs:
        try:
            val = json.loads(b.value_json or "{}")
        except (TypeError, ValueError):
            continue
        if val.get("clarification_status") == "needs_clarification" and b.confidence >= CURIOSITY_SIGNAL_THRESHOLD:
            return signals["needs_clarification"]

    # 2. 有低置信度但接近阈值的模式信念（D3/D5，隐约感知到但不确定）
    for b in beliefs:
        if (
            b.dimension in ("D3", "D5")
            and LOW_CONFIDENCE_PATTERN_FLOOR <= b.confidence < LOW_CONFIDENCE_PATTERN_CEIL
            and b.layer == "L4"
        ):
            return signals["low_confidence_pattern"]

    return None


def curiosity_signal_with_topics(
    session: Session, user_id: str, current_topics: list[str], language: str = ""
) -> str | None:
    """好奇心信号 + 新话题检测（需要当前轮次的 topics 输入）。"""
    # 先检查基础好奇心信号。
    base = curiosity_signal(session, user_id, language)
    if base:
        return base

    # 新话题检测：当前轮次的话题中，有没有用户从未提过的？
    if not current_topics:
        return None
    beliefs = list_active_beliefs(session, user_id, dimensions=("D1",), limit=20)
    known_topics = {b.key for b in beliefs}
    new_topics = [t for t in current_topics if t not in known_topics]
    if new_topics:
        is_en = language == "en"
        return CURIOSITY_SIGNALS_EN["new_topic"] if is_en else CURIOSITY_SIGNALS_ZH["new_topic"]
    return None


def _render_life_events(session: Session, user_id: str, language: str, now: datetime) -> str | None:
    """渲染最近的非过期生活事件，最多 MAX_LIFE_EVENTS 条。"""
    from psych_support_bot.infra.db.profile_repositories import list_active_beliefs

    beliefs = list_active_beliefs(session, user_id, dimensions=("D1",), limit=20)
    events: list[str] = []
    is_en = _is_en(language)
    for b in beliefs:
        if len(events) >= MAX_LIFE_EVENTS:
            break
        try:
            val = json.loads(b.value_json or "{}")
        except (TypeError, ValueError):
            continue
        if val.get("event_type") != "life_event":
            continue
        # 过期检查
        expires = val.get("expires_at")
        if expires:
            try:
                exp_dt = datetime.fromisoformat(expires)
                if exp_dt.tzinfo is None:
                    exp_dt = exp_dt.replace(tzinfo=UTC)
                if exp_dt < now:
                    continue
            except (ValueError, TypeError):
                pass
        summary = str(val.get("summary") or "")
        if not summary:
            continue
        time_tag = _time_ago_label(b, language)
        if is_en:
            events.append(f"{summary}" + (f" ({time_tag})" if time_tag else ""))
        else:
            events.append(f"{summary}" + (f"（{time_tag}）" if time_tag else ""))
    if not events:
        return None
    header = "Recent events" if is_en else "近期事件"
    return f"{header}：{('；' if not is_en else '; ').join(events)}"


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
    from psych_support_bot.infra.db.profile_repositories import is_profile_memory_enabled

    if not is_profile_memory_enabled(session, user_id):
        return None
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

    # D5 位置摘要：MI 改变连续谱的当前位置，固定次槽不参与装箱竞争。
    d5_pos = _d5_position_summary(session, user_id, language)
    if d5_pos:
        items.append(d5_pos)

    # Part C：生活事件窗口——最近的非过期事件，固定第三槽。
    life_events = _render_life_events(session, user_id, language, render_now)
    if life_events:
        items.append(life_events)

    # 加权打分 + 过滤零相关 + 多样性限制（借鉴知识库检索机制）。
    topic_set = set(topics)
    all_beliefs = list_active_beliefs(session, user_id)
    scored = []
    for belief in all_beliefs:
        s = score_belief(belief, topics=topic_set, risk_boost=risk_boost, now=render_now)
        if s > 0:
            scored.append((s, belief))
    scored.sort(key=lambda x: (x[0], x[1].last_evidence_at or ""), reverse=True)

    # 多样性：每个维度最多 _MAX_PER_DIMENSION 条，防止单维度占满槽位。
    dim_counts: dict[str, int] = {}
    for _score, belief in scored:
        dim = belief.dimension
        count = dim_counts.get(dim, 0)
        if count >= _MAX_PER_DIMENSION:
            continue
        text = _belief_item(belief, language)
        if text:
            items.append(text)
            dim_counts[dim] = count + 1

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
        "profile render: chars=%d budget=%d items=%d dropped=%d",
        used,
        budget,
        len(rendered),
        dropped,
    )
    return block or None
