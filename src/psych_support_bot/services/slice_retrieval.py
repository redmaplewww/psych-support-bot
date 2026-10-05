"""P5 画像驱动切片检索。

为当前轮检索"相关历史切片摘要"（SliceSummary），作为背景参考注入
memory_summary——与 L1 逐字上下文的分工：本次对话 = slice_context
（逐字，话题边界内），相关历史 = 完成切片的摘要（压缩，跨话题检索）。

评分权重（docs/profile-slice-integration.md 联动点 4）：
- 0.5 时间衰减：越近的对话越相关（分段函数，同设计文档 §2.3.2）；
- 0.3 主题匹配：摘要 topics 与匹配集的重叠率——匹配集 = 用户画像 D1
  active 信念 keys ∪ 当前消息 detect_topics 命中（首轮即可命中刚聊过的
  主题，不等画像沉淀出 D1）；
- 0.2 练习效果：画像 D4 中 effect=worked 的练习 tag 出现在摘要文本中
  （"上次呼吸练习有用"→ 同类话题的历史对话值得再提）。

红线：摘要输入已在 P4 生成侧剔除危机轮消息；本模块只读不写，检索失败
fail-open（返回空列表），绝不阻断对话。
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from psych_support_bot.ai.knowledge.index import detect_topics
from psych_support_bot.ai.profile.display_dict import friendly_label
from psych_support_bot.infra.db.models import SliceSummary, utcnow

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# 综合得分权重（设计文档联动点 4）。
W_TIME = 0.5
W_TOPIC = 0.3
W_EXERCISE = 0.2

CANDIDATE_POOL = 20  # 候选池上限（按时间取最近 N 条摘要再精排）
MAX_SLICES = 3  # 注入条数上限（memory_summary 预算有限，宁缺勿滥）
MAX_SUMMARY_LINE_CHARS = 120  # 单条摘要注入截断


def calculate_relevance_score_from_days(days_ago: int) -> float:
    """时间衰减分段函数（设计文档 §2.3.2 原样）：1d/3d/7d/30d 档位。"""
    if days_ago <= 1:
        return 1.0
    if days_ago <= 3:
        return 0.8
    if days_ago <= 7:
        return 0.6
    if days_ago <= 30:
        return 0.3
    return 0.1


def _days_ago(created_at, now) -> int:
    """SQLite 取回的 naive datetime 按 UTC 解释（与渲染层 belief_activity 同口径）。"""
    created = created_at.replace(tzinfo=None) if created_at.tzinfo else created_at
    ref = now.replace(tzinfo=None) if now.tzinfo else now
    return max((ref - created).days, 0)


def _profile_signals(session: Session, user_id: str) -> tuple[set[str], set[str]]:
    """画像驱动的两个匹配集：D1 主题 keys、D4 worked 练习 tags。"""
    from psych_support_bot.infra.db.profile_repositories import is_profile_memory_enabled, list_active_beliefs

    if not is_profile_memory_enabled(session, user_id):
        return set(), set()

    d1_topics: set[str] = set()
    worked_tags: set[str] = set()
    for belief in list_active_beliefs(session, user_id):
        if belief.dimension == "D1":
            d1_topics.add(belief.key)
        elif belief.dimension == "D4":
            try:
                value = json.loads(belief.value_json or "{}")
            except (TypeError, ValueError):
                value = {}
            if value.get("effect") == "worked":
                worked_tags.add(belief.key)
    return d1_topics, worked_tags


def score_slice_summary(
    summary: SliceSummary,
    *,
    match_topics: set[str],
    worked_tags: set[str],
    now,
) -> float:
    """单条摘要的相关性得分（纯函数，[0,1]）。"""
    try:
        topics = json.loads(summary.topics or "[]")
    except (TypeError, ValueError):
        topics = []
    topics = {str(t) for t in topics} if isinstance(topics, list) else set()

    time_score = calculate_relevance_score_from_days(_days_ago(summary.created_at, now))
    topic_score = len(match_topics & topics) / max(len(match_topics), 1) if match_topics else 0.0

    exercise_score = 0.0
    if worked_tags:
        hay = f"{summary.summary_text or ''} {topics}".lower()
        exercise_score = 1.0 if any(tag.lower() in hay for tag in worked_tags) else 0.0

    return round(W_TIME * time_score + W_TOPIC * topic_score + W_EXERCISE * exercise_score, 4)


def retrieve_relevant_slices(
    session: Session,
    user_id: str,
    current_message: str,
    *,
    exclude_slice_id: str = "",
    max_slices: int = MAX_SLICES,
) -> list[SliceSummary]:
    """检索与当前用户/当前话题最相关的历史切片摘要（fail-open 返回空表）。

    只返回有摘要正文的行（空正文 = 摘要生成失败或全危机段，不注入）。
    """
    try:
        candidates = (
            session.query(SliceSummary)
            .filter(SliceSummary.user_id == user_id)
            .order_by(SliceSummary.created_at.desc())
            .limit(CANDIDATE_POOL)
            .all()
        )
        candidates = [c for c in candidates if c.slice_id != exclude_slice_id and (c.summary_text or "").strip()]
        if not candidates:
            return []

        d1_topics, worked_tags = _profile_signals(session, user_id)
        current_topics = {str(t) for t in detect_topics(current_message or "")}
        match_topics = d1_topics | current_topics

        now = utcnow()
        scored = sorted(
            candidates,
            key=lambda s: (
                score_slice_summary(s, match_topics=match_topics, worked_tags=worked_tags, now=now),
                s.created_at,
            ),
            reverse=True,
        )
        top = scored[:max_slices]
        logger.info(
            "Slice retrieval: pool=%d matched_topics=%d worked=%d returned=%d",
            len(candidates),
            len(match_topics),
            len(worked_tags),
            len(top),
        )
        return top
    except Exception:  # noqa: BLE001 - optional retrieval is fail-open
        logger.warning("Slice retrieval failed; skipping relevant history")
        return []


def _render_topics_label(topics_json: str, language: str) -> str:
    try:
        topics = json.loads(topics_json or "[]")
    except (TypeError, ValueError):
        topics = []
    labels = [friendly_label(t) or str(t) for t in topics[:2] if friendly_label(t) or t]
    return "、".join(labels)


def render_slice_history_block(
    summaries: list[SliceSummary], *, language: str = "zh", max_items: int = MAX_SLICES
) -> str:
    """检索结果 → memory_summary 背景块（标注来源与时间线边界）。

    头部的"不是用户刚说的话"提示与 user_history_text 隔离通道同源：
    历史摘要不能被当成当前情绪表达做扫描。
    """
    items = [s for s in summaries[:max_items] if (s.summary_text or "").strip()]
    if not items:
        return ""
    en = language.strip().lower() == "en"
    header = (
        "[Relevant history - summaries of past conversations; background reference only, "
        "NOT what the user just said; never mix into the current timeline]"
        if en
        else "【相关历史】既往对话的摘要（背景参考；不是用户刚说的话，不要与本次对话混淆时间线）"
    )
    now = utcnow()
    lines = [header]
    for s in items:
        days = _days_ago(s.created_at, now)
        if en:
            when = "today" if days <= 0 else ("yesterday" if days == 1 else f"{days} days ago")
        else:
            when = "今天" if days <= 0 else ("昨天" if days == 1 else f"{days}天前")
        label = _render_topics_label(s.topics, language)
        prefix = f"- [{when}]" + (f"（{label}）" if label and not en else f" ({label})" if label else "")
        text = (s.summary_text or "").strip()
        if len(text) > MAX_SUMMARY_LINE_CHARS:
            text = text[: MAX_SUMMARY_LINE_CHARS - 1] + "…"
        lines.append(f"{prefix} {text}")
    return "\n".join(lines)
