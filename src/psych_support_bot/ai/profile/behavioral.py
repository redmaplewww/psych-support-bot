"""行为信号层：从现有对话数据中提取行为特征。

不创建画像信念——行为信号的用途是喂给好奇心注入，
在不确定时自然地问一句。信号本身带着噪声（网络延迟、
屏幕外的世界），所以不做推断，只做观察。

设计原则：
- 信号是观察，不是诊断
- 异常是对话入口，不是数据点
- 用户的解释才是真正的数据
"""

from __future__ import annotations

import contextlib
from datetime import UTC, datetime
from typing import Any

from psych_support_bot.ai.profile.constants import (
    DEFAULT_SLEEP_WINDOW,
    MESSAGE_LENGTH_DROP_RATIO,
    RESPONSE_DELAY_MAX_SECONDS,
    RESPONSE_DELAY_MIN_SECONDS,
    VAD_BRIEF_SPEECH_MS,
    VAD_HESITATION_MIN_SPEECH_MS,
    VAD_HESITATION_PAUSE_COUNT,
)


def detect_behavioral_signals(
    *,
    user_message: str,
    bot_message_timestamp: datetime | None = None,
    user_message_timestamp: datetime | None = None,
    recent_message_lengths: list[int] | None = None,
    session_hour: int = -1,
    vad_metadata: dict | None = None,
    sleep_window: tuple[int, int] | None = None,
) -> list[dict[str, Any]]:
    """检测当前轮次的行为异常信号。

    返回信号列表，每个信号是 {"type": str, "detail": str, "question_zh": str, "question_en": str}。
    不做推断——只提供"可以问什么"的建议。

    参数：
    - vad_metadata: 前端 VAD 元数据 {startAt, endAt, speechDurationMs, pauseCount}
    - sleep_window: 用户的睡眠窗口 (start_hour, end_hour)，来自 UserTimeProfile；
      None 时使用默认 23:00-05:00
    """
    signals: list[dict[str, Any]] = []

    # 1. VAD 信号：语音中的停顿（比文本延迟更精确的行为信号）
    if vad_metadata:
        pause_count = vad_metadata.get("pauseCount", 0)
        speech_ms = vad_metadata.get("speechDurationMs", 0)
        # 说话中多次停顿（≥3 次）：可能在犹豫或组织语言
        if pause_count >= VAD_HESITATION_PAUSE_COUNT and speech_ms > VAD_HESITATION_MIN_SPEECH_MS:
            signals.append(
                {
                    "type": "speech_hesitation",
                    "detail": f"pauses={pause_count}, duration={speech_ms}ms",
                    "question_zh": "你刚才说的时候好像停了好几次，是有些地方不太好表达吗？",
                    "question_en": "You paused several times while speaking — is something hard to put into words?",
                }
            )
        # 说话时间异常短（<2 秒且有内容）：可能是回避或敷衍
        elif speech_ms > 0 and speech_ms < VAD_BRIEF_SPEECH_MS and len(user_message or "") > 5:
            signals.append(
                {
                    "type": "brief_speech",
                    "detail": f"duration={speech_ms}ms, len={len(user_message)}",
                    "question_zh": "你刚才说得很快，是不太想聊这个吗？",
                    "question_en": "You spoke very quickly — are you not in the mood to talk about this?",
                }
            )

    # 2. 回复延迟异常（>5 分钟，排除网络噪声的阈值）
    #    有 VAD 元数据时用客户端 startAt（更接近用户真实思考时间），
    #    否则用服务端消息时间戳（包含网络延迟+STT 处理时间）。
    effective_user_ts = user_message_timestamp
    if vad_metadata and vad_metadata.get("startAt"):
        with contextlib.suppress(TypeError, ValueError, OSError):
            effective_user_ts = datetime.fromtimestamp(vad_metadata["startAt"] / 1000, tz=UTC)
    if bot_message_timestamp and effective_user_ts:
        if bot_message_timestamp.tzinfo is None:
            bot_message_timestamp = bot_message_timestamp.replace(tzinfo=UTC)
        if effective_user_ts.tzinfo is None:
            effective_user_ts = effective_user_ts.replace(tzinfo=UTC)
        latency = (effective_user_ts - bot_message_timestamp).total_seconds()
        if RESPONSE_DELAY_MIN_SECONDS < latency < RESPONSE_DELAY_MAX_SECONDS:  # 5-60 分钟：可能是有意义的停顿
            signals.append(
                {
                    "type": "long_pause",
                    "detail": f"{int(latency // 60)}min",
                    "question_zh": "你刚才好像停了一会儿，是发生了什么吗？",
                    "question_en": "You seem to have paused for a moment — did something happen?",
                }
            )

    # 3. 消息长度突变（比近期平均短 60%+）
    if recent_message_lengths and len(recent_message_lengths) >= 3:
        avg_len = sum(recent_message_lengths) / len(recent_message_lengths)
        current_len = len(user_message or "")
        if avg_len > 20 and current_len < avg_len * MESSAGE_LENGTH_DROP_RATIO and current_len > 0:
            signals.append(
                {
                    "type": "short_message",
                    "detail": f"{current_len} vs avg {int(avg_len)}",
                    "question_zh": "你今天的回复比之前简短一些，是累了还是不太想聊这个？",
                    "question_en": "Your message is shorter than usual — are you tired or not in the mood to talk about this?",
                }
            )

    # 4. 深夜来访：用用户的睡眠窗口代替固定 23:00-05:00。
    #    sleep_window 来自 UserTimeProfile（从打卡行为推断），格式 (start_hour, end_hour)。
    if session_hour >= 0:
        if sleep_window:
            sleep_start, sleep_end = sleep_window
            # 判断 session_hour 是否在睡眠窗口内（处理跨午夜）
            if sleep_start > sleep_end:
                in_sleep = session_hour >= sleep_start or session_hour < sleep_end
            else:
                in_sleep = sleep_start <= session_hour < sleep_end
        else:
            in_sleep = session_hour >= DEFAULT_SLEEP_WINDOW[0] or session_hour <= DEFAULT_SLEEP_WINDOW[1]
        if in_sleep:
            signals.append(
                {
                    "type": "late_night",
                    "detail": f"hour={session_hour}",
                    "question_zh": "这么晚了还没休息，是睡不着还是有什么事在心里？",
                    "question_en": "It's quite late — are you having trouble sleeping, or is something on your mind?",
                }
            )

    # 限制每轮最多 1 个信号（不轰炸用户）
    return signals[:1] if signals else []
