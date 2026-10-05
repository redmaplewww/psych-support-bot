"""对话切片管理器。

核心职责：
1. 检测是否需要创建新切片（时间/主题/显式切换）
2. 管理切片生命周期（创建/更新/完成）
3. 提供切片内上下文
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING
from uuid import uuid4

from psych_support_bot.infra.db.models import ConversationSlice, Message, utcnow
from psych_support_bot.services.time_profile import (
    get_user_time_profile,
    is_sleep_boundary,
)

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


# 显式切换关键词（中英文）
EXPLICIT_SWITCH_MARKERS = [
    "换个话题",
    "聊点别的",
    "不想说这个",
    "我想问",
    "有个新",
    "另一个问题",
    "change topic",
    "talk about something else",
    "different topic",
    "something else",
]

# 告别关键词
GOODBYE_MARKERS = [
    "谢谢",
    "再见",
    "我先去",
    "拜拜",
    "good bye",
    "thank you",
    "bye",
    "gotta go",
]


def should_create_new_slice(
    last_slice: ConversationSlice | None,
    user_message: str,
    user_id: str,
    session: Session,
) -> tuple[bool, str, float]:
    """
    判断是否需要创建新切片。

    返回: (是否切片, 原因, 置信度)

    Args:
        last_slice: 最近的切片（None 表示首次对话）
        user_message: 用户当前消息
        user_id: 用户 ID
        session: 数据库会话

    Returns:
        (should_slice, reason, confidence)
    """
    if last_slice is None:
        return True, "first_message", 1.0

    # 1. 时间维度（动态自适应）
    current_time = utcnow().replace(tzinfo=None)
    last_updated = last_slice.updated_at.replace(tzinfo=None) if last_slice.updated_at.tzinfo else last_slice.updated_at
    hours_since = (current_time - last_updated).total_seconds() / 3600
    time_profile = get_user_time_profile(session, user_id)

    # 获取动态阈值
    short_threshold = time_profile["short_gap_threshold"]  # 小时
    long_threshold = time_profile["long_gap_threshold"]  # 小时
    sleep_window = time_profile["sleep_window"]

    # 检查是否跨睡眠边界
    sleep_crossed = is_sleep_boundary(last_slice.updated_at, utcnow(), sleep_window)

    if sleep_crossed:
        # 跨睡眠边界：几乎确定是新对话
        logger.info("Sleep boundary detected; window=%s", sleep_window)
        return True, "sleep_boundary", 0.95
    elif hours_since > long_threshold:
        # 超过长间隔阈值：高置信度新对话
        logger.info("Long gap detected: %.1fh > %.1fh", hours_since, long_threshold)
        return True, f"time_gap_{int(hours_since)}h", 0.9
    elif hours_since > short_threshold:
        # 超过短间隔阈值：中等置信度，需结合其他信号
        time_signal = min(0.8, (hours_since - short_threshold) / (long_threshold - short_threshold))
    else:
        time_signal = 0.0

    # 2. 显式切换信号
    message_lower = user_message.lower()
    for marker in EXPLICIT_SWITCH_MARKERS:
        if marker in message_lower:
            logger.info("Explicit topic switch detected")
            return True, "explicit_switch", 0.9

    # 3. 主题相似度（TODO：Phase 3 实现主题提取）
    # current_topic_vec = extract_topic_vector(user_message)
    # if last_slice.topic_vector:
    #     similarity = cosine_similarity(current_topic_vec, last_slice.topic_vector)
    #     if similarity < 0.3:
    #         topic_signal = 0.8
    #     else:
    #         topic_signal = 0.0
    # else:
    #     topic_signal = 0.0
    topic_signal = 0.0  # 暂时不启用主题检测

    # 4. 结束信号后的新消息
    last_messages = get_slice_messages(session, last_slice.id, limit=2)
    if last_messages:
        last_content = last_messages[-1].content.lower() if last_messages else ""
        if any(marker in last_content for marker in GOODBYE_MARKERS):
            logger.info("New message after goodbye detected")
            return True, "after_goodbye", 0.85

    # 综合判定（时间信号权重提升）
    combined_score = max(time_signal, topic_signal)
    if combined_score > 0.6:
        logger.info("Topic shift detected: time_signal=%.2f topic_signal=%.2f", time_signal, topic_signal)
        return True, "topic_shift", combined_score

    return False, "continue", 1.0 - combined_score


def get_slice_messages(session: Session, slice_id: str, limit: int | None = None) -> list[Message]:
    """
    获取切片内的消息列表。

    Args:
        session: 数据库会话
        slice_id: 切片 ID
        limit: 最多返回的消息数（None 表示全部）

    Returns:
        消息列表（按时间正序）
    """
    query = session.query(Message).filter_by(slice_id=slice_id).order_by(Message.created_at)
    if limit:
        query = query.limit(limit)
    return list(query.all())


def build_slice_context(session: Session, slice_id: str, max_turns: int = 20) -> list[dict]:
    """
    构建切片内的完整上下文（逐字对话）。

    与现有 recent_history 不同：
    - recent_history: 固定取最近 6 轮（跨切片）
    - slice_context: 取当前切片内的所有对话（最多 max_turns）

    Args:
        session: 数据库会话
        slice_id: 切片 ID
        max_turns: 最多返回的轮数

    Returns:
        对话上下文列表，格式：[{"role": "user", "content": "..."}, ...]
    """
    messages = get_slice_messages(session, slice_id, limit=max_turns * 2)  # *2 因为一轮有 user+assistant
    return [
        {"role": msg.role, "content": msg.content}
        for msg in messages
        if msg.role in {"user", "assistant"} and (msg.content or "").strip()
    ]


class SliceManager:
    """对话切片管理器。"""

    def get_or_create_slice(
        self,
        session: Session,
        user_id: str,
        session_id: str,
        user_message: str,
    ) -> ConversationSlice:
        """
        获取或创建当前对话切片。

        核心逻辑：
        1. 查询该 session 下的最新 active 切片
        2. 判断是否需要新建切片
        3. 返回切片 ID

        Args:
            session: 数据库会话
            user_id: 用户 ID
            session_id: 会话 ID
            user_message: 用户当前消息

        Returns:
            ConversationSlice 对象
        """
        # 查询最新活跃切片
        last_slice = (
            session.query(ConversationSlice)
            .filter_by(user_id=user_id, session_id=session_id, status="active")
            .order_by(ConversationSlice.updated_at.desc())
            .first()
        )

        # 判断是否切片
        should_slice, reason, confidence = should_create_new_slice(last_slice, user_message, user_id, session)

        if should_slice:
            # 关闭旧切片
            if last_slice:
                last_slice.status = "completed"
                # end_message_id 在消息保存后更新
                session.commit()

                # P4 切片完成钩子：摘要生成 + 主题继承（fail-open，绝不阻塞新切片创建）。
                self._finalize_completed_slice(session, last_slice)

            # 创建新切片
            new_slice = ConversationSlice(
                id=f"slice-{uuid4()}",
                session_id=session_id,
                user_id=user_id,
                primary_topic="",  # TODO: Phase 3 实现主题提取
                boundary_reason=reason,
                boundary_confidence=confidence,
                turn_count=0,
            )
            session.add(new_slice)
            session.commit()

            logger.info("Created conversation slice: reason=%s confidence=%.2f", reason, confidence)
            return new_slice
        else:
            # 复用现有切片
            last_slice.turn_count += 1
            last_slice.updated_at = utcnow()
            session.commit()
            logger.debug("Reusing conversation slice: turn_count=%d", last_slice.turn_count)
            return last_slice

    @staticmethod
    def _finalize_completed_slice(session: Session, completed: ConversationSlice) -> None:
        """切片完成后的收尾钩子（P4）。fail-open：任何失败只记日志。

        开关 ENABLE_SLICE_BASED_EXTRACTION 独立于切片本体（摘要是一次额外
        LLM 调用，单独灰度）。做两件事：
        1. 生成切片摘要（SliceSummary 行 + 不覆盖式补齐 primary_topic）；
        2. end_message_id 回填——此刻切片内消息已全部落库（本轮 user/assistant
           在 save_conversation_result 保存，切片关闭发生在下一轮到达时），
           取最后一条关联消息的 id。

        同步执行的取舍：切片关闭本就挂在用户下一轮请求路径上（延迟预算
        ~百毫秒级），LLM 摘要在生成侧自带降级（失败 → 确定性拼接），最坏
        情形退化为一次词典扫描。
        """
        from psych_support_bot.infra.config.settings import get_settings

        if not get_settings().enable_slice_based_extraction:
            return
        try:
            from psych_support_bot.services.slice_summary import generate_slice_summary

            generate_slice_summary(session, completed.id)
        except Exception:  # noqa: BLE001 - optional slice summary is fail-open
            logger.warning("Slice summary hook failed; slice lifecycle unaffected")
            session.rollback()

        # K3 综合引擎已移至异步 Worker（profile_evolution.py），
        # 由 enqueue_evolution_job 触发，不再同步执行。

        try:
            last_msg = session.query(Message).filter_by(slice_id=completed.id).order_by(Message.id.desc()).first()
            if last_msg is not None:
                completed.end_message_id = last_msg.id
                first_msg = session.query(Message).filter_by(slice_id=completed.id).order_by(Message.id.asc()).first()
                if first_msg is not None:
                    completed.start_message_id = first_msg.id
        except Exception:  # noqa: BLE001 - boundary backfill must not break the turn
            logger.warning("Slice boundary message backfill failed")
