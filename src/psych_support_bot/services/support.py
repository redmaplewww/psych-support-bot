"""服务层共享纯 helper（语言检测 / 时间工具）。

自 services/conversation.py 拆出（P3 问卷状态机拆分）：conversation
与 questionnaire_flow 双向需要，独立成模块避免循环导入。
"""

import re
from datetime import UTC, datetime
from typing import Any


def _has_chinese(text: str) -> bool:
    return any("\u4e00" <= char <= "\u9fff" for char in text)


def _is_language_neutral(text: str) -> bool:
    """Return True when the text contains no Chinese characters and no
    ASCII letter words, meaning the language cannot be reliably detected.
    Examples: pure numbers ("3"), punctuation ("..."), single letters ("y")."""
    stripped = text.strip()
    if not stripped:
        return True
    if stripped.isdigit():
        return True

    has_ascii_words = bool(re.search(r"[a-zA-Z]{3,}", stripped))
    has_chinese = _has_chinese(stripped)
    return not has_chinese and not has_ascii_words


def _detect_expected_language(
    current_message: str,
    prior_messages: list[Any] | None = None,
) -> str:
    """Determine the expected conversation language.

    If the current message contains enough linguistic signal (Chinese
    characters or ASCII words), use it directly. Otherwise, walk
    backwards through *prior_messages* (role == 'user') to find the
    most recent message with clear language signal and inherit its
    language. Falls back to 'en' when nothing is found.
    """
    if not _is_language_neutral(current_message):
        return "zh" if _has_chinese(current_message) else "en"

    if prior_messages:
        for msg in reversed(prior_messages):
            if getattr(msg, "role", None) != "user":
                continue
            content = getattr(msg, "content", "")
            if _is_language_neutral(content):
                continue
            return "zh" if _has_chinese(content) else "en"

    return "en"


def _days_since(dt: datetime) -> int:
    """Whole days since *dt*, tolerating both naive and aware timestamps
    (SQLite round-trips drop tzinfo)."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return max((datetime.now(UTC) - dt).days, 0)
