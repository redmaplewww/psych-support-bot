"""Regression tests for questionnaire-mode guardrails (Langfuse 巡检 2026-09-04):
strict answer parsing and emotional-disclosure pause mid-questionnaire."""

import pytest

from psych_support_bot.domain.assessments.service import (
    detect_emotional_disclosure,
    parse_questionnaire_answer,
)

# --- Strict answer parsing ---


@pytest.mark.parametrize(
    "message,expected",
    [
        ("3", 3),
        ("0", 0),
        ("3分", 3),
        ("3。", 3),
        ("3，", 3),
        ("选3", 3),
        ("我选2", 2),
        ("第3项", 3),
        ("答案是1", 1),
        ("  2  ", 2),
    ],
)
def test_parse_accepts_structured_answers(message: str, expected: int) -> None:
    assert parse_questionnaire_answer(message, "phq9") == expected


@pytest.mark.parametrize(
    "message",
    [
        "我最近3天都很焦虑",  # prose containing a number — must NOT be swallowed
        "我最近还感到很焦虑",
        "最近心情不太好",
        "第3题是什么来着",  # question about the quiz itself
        "能重复一遍选项吗",
        "13分",  # out of range (phq9 item max is 3)
    ],
)
def test_parse_rejects_unstructured_prose(message: str) -> None:
    assert parse_questionnaire_answer(message, "phq9") is None


def test_parse_rejects_out_of_range_structured() -> None:
    assert parse_questionnaire_answer("9分", "phq9") is None


def test_parse_option_aliases_still_work() -> None:
    assert parse_questionnaire_answer("几乎每天", "phq9") == 3
    assert parse_questionnaire_answer("完全没有", "gad7") == 0
    assert parse_questionnaire_answer("偶尔", "phq9") == 1
    assert parse_questionnaire_answer("严重", "isi") == 3


# --- Emotional disclosure detection ---


def test_detect_emotional_disclosure_matches_common_phrases() -> None:
    assert detect_emotional_disclosure("我最近还感到很焦虑") is True
    assert detect_emotional_disclosure("最近心情不太好") is True
    assert detect_emotional_disclosure("我快撑不住了") is True
    assert detect_emotional_disclosure("这两天一直睡不着") is True


def test_detect_emotional_disclosure_negative() -> None:
    assert detect_emotional_disclosure("3") is False
    assert detect_emotional_disclosure("选2") is False
    assert detect_emotional_disclosure("跳过") is False
