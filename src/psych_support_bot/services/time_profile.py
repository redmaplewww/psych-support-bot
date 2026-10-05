"""用户时间画像计算模块。

计算用户的对话间隔统计，生成动态时间阈值用于切片检测。
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING

from psych_support_bot.infra.db.models import (
    ConversationSession,
    Message,
    UserTimeProfile,
)

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


# 最小样本量要求
MIN_MESSAGES_FOR_PROFILE = 5
MIN_VALID_GAPS_FOR_PROFILE = 3

# 间隔统计过滤：超过 7 天的间隔视为"新一轮使用"，不计入同轮统计
MAX_GAP_DAYS = 7


def calculate_time_profile(session: Session, user_id: str) -> dict[str, float | tuple[int, int] | str]:
    """
    分析用户历史对话，计算时间画像。

    返回格式：
    {
        "short_gap_threshold": 0.5,  # 小时
        "long_gap_threshold": 6.0,   # 小时
        "sleep_window": (23, 7),     # (入睡时, 起床时)
        "frequency_tier": "medium",
        "confidence": 0.8,           # 画像置信度（样本量相关）
    }
    """
    # 查询用户所有消息的时间戳
    messages = list(
        session.query(Message.created_at)
        .join(ConversationSession, Message.session_id == ConversationSession.id)
        .filter(ConversationSession.user_id == user_id)
        .order_by(Message.created_at)
        .all()
    )

    if len(messages) < MIN_MESSAGES_FOR_PROFILE:
        # 新用户：使用保守默认值
        logger.debug("Insufficient messages for time profile; using defaults")
        return {
            "short_gap_threshold": 1.0,  # 1 小时
            "long_gap_threshold": 12.0,  # 12 小时
            "sleep_window": (23, 7),
            "frequency_tier": "unknown",
            "confidence": 0.3,
        }

    # 计算消息间隔（分钟）
    timestamps = [msg.created_at for msg in messages]
    gaps_minutes = [(timestamps[i] - timestamps[i - 1]).total_seconds() / 60 for i in range(1, len(timestamps))]

    # 过滤异常值（超过 MAX_GAP_DAYS 的间隔视为"新一轮使用"）
    max_gap_minutes = MAX_GAP_DAYS * 24 * 60
    valid_gaps = [g for g in gaps_minutes if g <= max_gap_minutes]

    if len(valid_gaps) < MIN_VALID_GAPS_FOR_PROFILE:
        # 有效样本不足
        logger.debug("Insufficient valid gaps for time profile; using defaults")
        return {
            "short_gap_threshold": 1.0,
            "long_gap_threshold": 12.0,
            "sleep_window": (23, 7),
            "frequency_tier": "unknown",
            "confidence": 0.4,
        }

    # 统计分位数（使用简化的百分位计算）
    valid_gaps_sorted = sorted(valid_gaps)
    n = len(valid_gaps_sorted)

    def percentile(data: list[float], p: float) -> float:
        """计算第 p 百分位数（0-100）。"""
        index = int(n * p / 100)
        return data[min(index, n - 1)]

    p25 = percentile(valid_gaps_sorted, 25) / 60  # 转小时
    median = percentile(valid_gaps_sorted, 50) / 60
    p75 = percentile(valid_gaps_sorted, 75) / 60
    mean = sum(valid_gaps) / len(valid_gaps) / 60

    # 阈值策略：
    # - short_gap_threshold = max(p25, 0.5h)  # 至少 30 分钟
    # - long_gap_threshold = min(p75 * 1.5, 24h)  # 最多 24 小时
    short_threshold = max(p25, 0.5)
    long_threshold = min(p75 * 1.5, 24.0)

    # 频率分类
    if median < 2:  # 中位数间隔 < 2 小时
        frequency_tier = "high"
    elif median < 12:  # 中位数间隔 < 12 小时
        frequency_tier = "medium"
    else:
        frequency_tier = "low"

    # 作息模式检测（简化版：找凌晨时段的空白）
    hours = [ts.hour for ts in timestamps]
    night_hours = [h for h in hours if 0 <= h < 6]
    if len(night_hours) / len(hours) < 0.05:  # 凌晨消息很少
        sleep_start = 23
        sleep_end = 7
    else:
        # 用户可能是夜猫子，使用默认值
        sleep_start = 1
        sleep_end = 9

    # 置信度：样本量越多越可信
    confidence = min(1.0, len(valid_gaps) / 50)  # 50+ 样本达到满置信度

    logger.info(
        f"Time profile: p25={p25:.1f}h, median={median:.1f}h, p75={p75:.1f}h, "
        f"tier={frequency_tier}, short={short_threshold:.1f}h, long={long_threshold:.1f}h, "
        f"confidence={confidence:.2f}"
    )

    return {
        "short_gap_threshold": short_threshold,
        "long_gap_threshold": long_threshold,
        "sleep_window": (sleep_start, sleep_end),
        "frequency_tier": frequency_tier,
        "confidence": confidence,
        "avg_gap_minutes": mean * 60,
        "median_gap_minutes": median * 60,
        "p25_gap_minutes": p25 * 60,
        "p75_gap_minutes": p75 * 60,
    }


def get_user_time_profile(session: Session, user_id: str) -> dict[str, float | tuple[int, int] | str]:
    """
    获取用户时间画像（带缓存）。

    策略：
    - 每 50 条新会话重新计算一次
    - 缓存到 UserTimeProfile 表
    """
    from psych_support_bot.infra.db.profile_repositories import is_profile_memory_enabled

    if not is_profile_memory_enabled(session, user_id):
        return {
            "short_gap_threshold": 1.0,
            "long_gap_threshold": 12.0,
            "sleep_window": (23, 7),
            "frequency_tier": "disabled",
            "confidence": 0.0,
        }
    profile = session.get(UserTimeProfile, user_id)

    # 判断是否需要更新
    if profile is None:
        # 首次计算
        computed = calculate_time_profile(session, user_id)
        profile = UserTimeProfile(
            user_id=user_id,
            avg_gap_minutes=computed.get("avg_gap_minutes", 0.0),
            median_gap_minutes=computed.get("median_gap_minutes", 0.0),
            p25_gap_minutes=computed.get("p25_gap_minutes", 0.0),
            p75_gap_minutes=computed.get("p75_gap_minutes", 0.0),
            sleep_start_hour=computed["sleep_window"][0],
            sleep_end_hour=computed["sleep_window"][1],
            frequency_tier=computed["frequency_tier"],
            short_gap_threshold_minutes=computed["short_gap_threshold"] * 60,
            long_gap_threshold_minutes=computed["long_gap_threshold"] * 60,
            total_sessions=1,
        )
        session.add(profile)
        session.commit()
        return computed

    # 检查是否需要重新计算（每 50 次会话）
    current_sessions = session.query(ConversationSession).filter_by(user_id=user_id).count()
    if current_sessions - profile.total_sessions >= 50:
        # 重新计算
        computed = calculate_time_profile(session, user_id)
        profile.avg_gap_minutes = computed.get("avg_gap_minutes", profile.avg_gap_minutes)
        profile.median_gap_minutes = computed.get("median_gap_minutes", profile.median_gap_minutes)
        profile.p25_gap_minutes = computed.get("p25_gap_minutes", profile.p25_gap_minutes)
        profile.p75_gap_minutes = computed.get("p75_gap_minutes", profile.p75_gap_minutes)
        profile.short_gap_threshold_minutes = computed["short_gap_threshold"] * 60
        profile.long_gap_threshold_minutes = computed["long_gap_threshold"] * 60
        profile.sleep_start_hour = computed["sleep_window"][0]
        profile.sleep_end_hour = computed["sleep_window"][1]
        profile.frequency_tier = computed["frequency_tier"]
        profile.total_sessions = current_sessions
        session.commit()
        logger.info("Updated time profile after %d sessions", current_sessions)
        return computed

    # 使用缓存
    return {
        "short_gap_threshold": profile.short_gap_threshold_minutes / 60,
        "long_gap_threshold": profile.long_gap_threshold_minutes / 60,
        "sleep_window": (profile.sleep_start_hour, profile.sleep_end_hour),
        "frequency_tier": profile.frequency_tier,
        "confidence": 1.0,  # 缓存数据默认高置信度
    }


def is_sleep_boundary(
    last_time: datetime,
    current_time: datetime,
    sleep_window: tuple[int, int],
) -> bool:
    """
    判断两个时间戳是否跨越睡眠边界。

    例如：
    - last_time: 2024-01-15 23:30
    - current_time: 2024-01-16 08:00
    - sleep_window: (23, 7)
    -> 返回 True（跨越了睡眠时段）

    Args:
        last_time: 上次消息时间
        current_time: 当前消息时间
        sleep_window: (入睡时, 起床时) 24 小时制

    Returns:
        是否跨越睡眠边界
    """
    # 统一处理时区：移除时区信息
    last_time_naive = last_time.replace(tzinfo=None) if last_time.tzinfo else last_time
    current_time_naive = current_time.replace(tzinfo=None) if current_time.tzinfo else current_time

    sleep_start, sleep_end = sleep_window

    # 简化判定：如果间隔 > 6 小时，且时间点符合睡眠模式
    hours_gap = (current_time_naive - last_time_naive).total_seconds() / 3600
    if hours_gap < 6:
        return False

    # 检查 last_time 是否在睡前时段，current_time 是否在起床后
    last_hour = last_time_naive.hour
    curr_hour = current_time_naive.hour

    if sleep_start > sleep_end:  # 跨日睡眠（如 23 点 -> 次日 7 点）
        last_in_sleep_start = last_hour >= sleep_start or last_hour < sleep_end
        curr_after_sleep = curr_hour >= sleep_end and curr_hour < sleep_start
        return last_in_sleep_start and curr_after_sleep
    else:  # 同日睡眠（如午休 13-15 点）
        last_in_sleep = sleep_start <= last_hour < sleep_end
        curr_after_sleep = curr_hour >= sleep_end
        return last_in_sleep and curr_after_sleep
