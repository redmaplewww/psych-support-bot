"""切片摘要生成（P4）。

当切片关闭（status → completed）时，为该切片生成一行 SliceSummary：
- 摘要正文：LLM 生成（fail-open：LLM 不可用/解析失败时降级为确定性
  拼接——取切片内用户消息前若干句，绝不因摘要失败阻塞切片生命周期）；
- key_points / topics：确定性路径为主（detect_topics 复用知识库主题闭集，
  与画像 D1 同口径），LLM 摘要只负责叙述化；
- 危机红线与画像层同构：safety_flag=True 的消息（危机轮 user/assistant
  双双标记）不进入摘要输入——高危内容不入切片摘要层，与"危机零提取"
  同一设计边界。

P4 与 P5 的分工：本模块只写 SliceSummary（供给检索候选池）；检索排序
（时间衰减 + 画像加权）在 slice_retrieval.py。
"""

from __future__ import annotations

import json
import logging

from sqlalchemy.orm import Session

from psych_support_bot.ai.knowledge.index import detect_topics
from psych_support_bot.infra.db.models import ConversationSlice, SliceSummary
from psych_support_bot.services.slice_manager import get_slice_messages

logger = logging.getLogger(__name__)

# 摘要正文的字符上限：摘要供检索注入（P5 经 memory_summary 进 prompt），
# 超长会挤占画像/知识区的预算——2 轮对话的忠实压缩通常 ≤300 字符。
MAX_SUMMARY_CHARS = 300
# 确定性降级摘要取的最多用户消息条数。
MAX_FALLBACK_USER_MESSAGES = 3
# 主题标签上限（与 llm_topics 的 ≤3 约定同源）。
MAX_TOPICS = 3

_SUMMARY_SYSTEM_PROMPT = (
    "You are the conversation-summarization module of a psych-support bot. "
    "Summarize ONE conversation segment (a completed topic slice within a session) "
    "in 1-2 Chinese sentences.\n"
    "Rules:\n"
    "- Describe what the USER talked about and any outcome (e.g. tried a breathing "
    "exercise, felt calmer); do not address the user, do not give advice.\n"
    "- Clinical-neutral observation language; never diagnose, never label.\n"
    "- If the segment shows crisis or self-harm content, output only "
    "'（该段含安全事件，未纳入摘要）'.\n"
    f"- Hard limit: {MAX_SUMMARY_CHARS} Chinese characters. Output the summary "
    "text only, no quotes, no headings."
)


def _safe_messages_for_summary(messages: list) -> list:
    """过滤危机轮消息：safety_flag=True 的行整对剔除（红线与画像提取同构）。"""
    return [m for m in messages if not bool(m.safety_flag)]


def deterministic_summary(messages: list) -> str | None:
    """确定性降级摘要：用户消息串接（最近 N 条），截断到字符上限。

    LLM 不可用时的兜底；也用于切片内容过短不值得调用 LLM 的情形。
    全部消息为空（或全被安全过滤剔除）时返回 None——不产生摘要行。
    """
    user_texts = [(m.content or "").strip() for m in messages if m.role == "user" and (m.content or "").strip()]
    if not user_texts:
        return None
    joined = "；".join(user_texts[:MAX_FALLBACK_USER_MESSAGES])
    if len(joined) > MAX_SUMMARY_CHARS:
        joined = joined[: MAX_SUMMARY_CHARS - 1] + "…"
    return joined


def build_summary_payload(messages: list) -> str:
    """组装摘要 LLM 的 payload：逐字对话（role: content 格式）。"""
    lines = [f"{m.role}: {m.content}" for m in messages if (m.content or "").strip()]
    return "[Conversation segment]\n" + "\n".join(lines)


def get_slice_topics(messages: list) -> list[str]:
    """切片主题标签（确定性）：用户消息合文本过 detect_topics 闭集。"""
    user_text = " ".join((m.content or "") for m in messages if m.role == "user")
    return detect_topics(user_text or "")[:MAX_TOPICS]


def find_candidate_primary_topic(messages: list) -> str:
    """切片首个非空主题标签——primary_topic 的确定性继承（联动点 2）。

    优先首个主题信号（话题通常由开场引入）；全空返回 ""（切片保持无主题，
    不影响既有渲染路径——primary_topic 默认空串）。
    """
    topics = get_slice_topics(messages)
    return topics[0] if topics else ""


def generate_slice_summary(session: Session, slice_id: str) -> SliceSummary | None:
    """为完成的切片生成摘要行。幂等：已有摘要直接返回。

    fail-open：LLM 失败走确定性降级；确定性也拿不到内容时不落行。
    异常向上抛由调用方（complete_slice 钩子）统一兜底记录，本次绝不因
    摘要失败阻塞切片状态流转。
    """
    existing = session.get(SliceSummary, slice_id)
    if existing is not None:
        return existing

    the_slice = session.get(ConversationSlice, slice_id)
    if the_slice is None:
        return None

    messages = _safe_messages_for_summary(get_slice_messages(session, slice_id))
    topic_labels = get_slice_topics(messages)

    summary_text = ""
    if messages:
        summary_text = _generate_summary_text_via_llm(messages) or deterministic_summary(messages) or ""

    row = SliceSummary(
        slice_id=slice_id,
        user_id=the_slice.user_id,
        summary_text=summary_text,
        key_points=json.dumps([], ensure_ascii=False),
        topics=json.dumps(topic_labels[:MAX_TOPICS], ensure_ascii=False),
        relevance_score=1.0,
    )
    session.add(row)

    # 联动点 2：primary_topic 确定性继承——"有则不动、无则补齐"（不覆盖
    # 将来 LLM/人工写的高质量值），并把主主题置顶到摘要 topics（检索匹配
    # 面与切片主主题保持一致）。
    if not the_slice.primary_topic:
        candidate = find_candidate_primary_topic(messages)
        if candidate:
            the_slice.primary_topic = candidate
    merged: list[str] = []
    for t in [the_slice.primary_topic, *topic_labels]:
        if t and t not in merged:
            merged.append(t)
    row.topics = json.dumps(merged[:MAX_TOPICS], ensure_ascii=False)
    return row


def _generate_summary_text_via_llm(messages: list) -> str | None:
    """LLM 摘要正文；不可用/空返回 None（调用方降级），异常不外泄。"""
    try:
        from psych_support_bot.infra.llm.generation import generate_profile_extraction

        raw = generate_profile_extraction(
            system_prompt=_SUMMARY_SYSTEM_PROMPT,
            payload_text=build_summary_payload(messages),
            fallback=lambda: "",
        )
        text = (raw or "").strip().strip('"').strip("'")
        if not text or text.startswith("（该段含安全事件"):
            return None
        if len(text) > MAX_SUMMARY_CHARS:
            text = text[: MAX_SUMMARY_CHARS - 1] + "…"
        return text
    except Exception:  # noqa: BLE001 - optional summary has a deterministic fallback
        logger.warning("Slice summary LLM failed; falling back to deterministic")
        return None
