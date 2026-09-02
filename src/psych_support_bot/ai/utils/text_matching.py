"""Shared text normalization and keyword matching utilities."""

import re
import unicodedata


def _normalize_text(text: str) -> tuple[str, str]:
    normalized = unicodedata.normalize("NFKC", text).casefold()
    collapsed = " ".join(normalized.split())
    compact = "".join(ch for ch in collapsed if ch.isalnum() or "\u4e00" <= ch <= "\u9fff")
    return collapsed, compact


def _is_chinese(text: str) -> bool:
    return bool(text and all("\u4e00" <= ch <= "\u9fff" for ch in text if ch.strip()))


def _contains_keyword(text: str, compact_text: str, keyword: str) -> bool:
    normalized_keyword, compact_keyword = _normalize_text(keyword)

    if _is_chinese(normalized_keyword):
        return normalized_keyword in text

    if len(compact_keyword) <= 3:
        pattern = re.escape(normalized_keyword)
        return bool(re.search(r"(?<![a-z])\b" + pattern + r"\b(?![a-z])", text, re.IGNORECASE))

    if normalized_keyword in text:
        return True
    return bool(compact_keyword) and compact_keyword in compact_text


def _match_any(text: str, compact_text: str, keywords: list[str]) -> bool:
    return any(_contains_keyword(text, compact_text, keyword) for keyword in keywords)
