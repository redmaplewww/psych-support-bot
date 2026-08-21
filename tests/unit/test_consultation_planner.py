"""Tests for B2.1: Cross-turn contradiction detection in consultation_planner."""

from typing import cast

from psych_support_bot.ai.nodes.consultation_planner import (
    _detect_cross_turn_contradiction,
    _extract_emotion_directions,
    plan_consultation,
)
from psych_support_bot.ai.schemas.messages import (
    ConversationMode,
    GeneratedReply,
    RiskResult,
)
from psych_support_bot.ai.schemas.state import GraphState


def _build_state(
    *,
    mode: ConversationMode = "support",
    user_message: str = "I feel okay",
    memory_summary: str = "",
    risk_level: str = "low",
) -> GraphState:
    return cast(
        GraphState,
        {
            "user_id": "test-user",
            "session_id": "session-1",
            "user_message": user_message,
            "memory_summary": memory_summary,
            "knowledge_context": "",
            "mode": mode,
            "risk_result": RiskResult(
                risk_level=risk_level,
                risk_types=[],
                needs_crisis_mode=False,
                reason="test",
            ),
            "generated_reply": GeneratedReply(
                text="",
                style=mode,
                includes_action_step=True,
            ),
            "session_summary": "",
            "topics": [],
            "fallback_used": False,
            "consultation_required": False,
            "consultation_agents": [],
            "consultation_notes": "",
            "consultation_opinions": [],
            "interview_stage": "engagement",
            "question_strategy": "open",
            "challenge_allowed": False,
            "loop_hint": "Start broad.",
        },
    )


# --- Emotion direction extraction ---


def test_negative_state_detected() -> None:
    assert _extract_emotion_directions("我最近很焦虑") == {"negative"}


def test_positive_state_detected() -> None:
    assert _extract_emotion_directions("我好多了") == {"positive"}


def test_neutral_state_returns_empty() -> None:
    assert _extract_emotion_directions("今天天气不错") == set()


def test_english_negative_detected() -> None:
    assert _extract_emotion_directions("I feel very depressed") == {"negative"}


def test_english_positive_detected() -> None:
    assert _extract_emotion_directions("I'm feeling much better now") == {"positive"}


# --- Cross-turn contradiction detection ---


def test_negative_to_positive_shift_detected() -> None:
    memory = "profile || recent summary | 我最近很焦虑，睡不着觉"
    current = "我今天好多了，不焦虑了"
    hint = _detect_cross_turn_contradiction(memory, current)
    assert hint is not None
    assert "improvement" in hint.lower() or "shift" in hint.lower()


def test_positive_to_negative_shift_detected() -> None:
    memory = "profile || summary | 我最近好多了，很开心"
    current = "我今天又很沮丧，很难过"
    hint = _detect_cross_turn_contradiction(memory, current)
    assert hint is not None
    assert "setback" in hint.lower() or "shift" in hint.lower()


def test_no_contradiction_when_same_direction() -> None:
    memory = "我最近很焦虑"
    current = "今天还是一样紧张"
    hint = _detect_cross_turn_contradiction(memory, current)
    assert hint is None


def test_no_contradiction_when_neutral_current() -> None:
    memory = "我最近很焦虑"
    current = "今天去买了点东西"
    hint = _detect_cross_turn_contradiction(memory, current)
    assert hint is None


def test_no_contradiction_with_empty_memory() -> None:
    hint = _detect_cross_turn_contradiction("", "我好多了")
    assert hint is None


def test_english_negative_to_positive_detected() -> None:
    memory = "profile || User (mode=support): I feel so depressed and hopeless."
    current = "I'm feeling much better today, actually good."
    hint = _detect_cross_turn_contradiction(memory, current)
    assert hint is not None
    assert "improvement" in hint.lower() or "shift" in hint.lower()


# --- Integration: plan_consultation with contradiction ---


def test_plan_consultation_injects_contradiction_hint() -> None:
    state = _build_state(
        user_message="我好多了，完全不焦虑了",
        memory_summary="我最近很焦虑，睡不着觉",
    )
    result = plan_consultation(state)
    assert "contradiction" in result["loop_hint"].lower()


def test_plan_consultation_no_contradiction_without_memory() -> None:
    state = _build_state(
        user_message="我很焦虑",
        memory_summary="",
    )
    result = plan_consultation(state)
    assert "contradiction" not in result["loop_hint"].lower()


def test_plan_consultation_keeps_original_hint_when_no_contradiction() -> None:
    state = _build_state(
        user_message="今天还是一样焦虑",
        memory_summary="我最近很焦虑",
    )
    result = plan_consultation(state)
    # Should not contain contradiction prefix
    assert "contradiction" not in result["loop_hint"].lower()
    # Should contain the original interview process hint
    assert len(result["loop_hint"]) > 0


# --- B2.1 修正后的边界测试 ---


def test_nfkc_normalization_fullwidth_anxious() -> None:
    """全角字符通过 NFKC 归一化后应被正确识别。"""
    # Fullwidth 'ａｎｘｉｏｕｓ' should match 'anxious' after NFKC
    assert _extract_emotion_directions("I feel ａｎｘｉｏｕｓ") == {"negative"}


def test_english_word_boundary_not_substring() -> None:
    """英文短词不应匹配子串——'sad' 不应匹配 'sandwich'。"""
    # 'sad' should NOT match inside 'sandwich' due to word boundary
    assert _extract_emotion_directions("I had a sandwich") == set()


def test_full_memory_snapshot_no_format_parsing_needed() -> None:
    """完整 memory snapshot 格式（含 || 和 | 分隔符）不需要解析格式。"""
    memory = "焦虑 || recent summary || assessment: PHQ-9=15 | 我最近很焦虑睡不着"
    current = "我今天好多了，完全不焦虑了"
    hint = _detect_cross_turn_contradiction(memory, current)
    assert hint is not None
    assert "improvement" in hint.lower() or "shift" in hint.lower()


def test_negated_negative_not_counted_as_negative() -> None:
    """否定后的负面词（如'不焦虑'）不应被计为 negative。"""
    assert _extract_emotion_directions("我不焦虑") == set()


def test_negated_negative_counted_as_positive_when_positive_also_present() -> None:
    """'不焦虑了'应被正面前缀词识别为 positive（因为'不焦虑了'在 positive 列表中）。"""
    assert _extract_emotion_directions("我不焦虑了，好多了") == {"positive"}


def test_no_false_positive_on_metadata_text() -> None:
    """memory 中的元数据文本不应误触发情绪方向。"""
    memory = "User (mode=support, risk=low): 今天去公园散步了"
    current = "我今天很开心"
    # memory has no negative/positive signal, current is positive → no contradiction
    hint = _detect_cross_turn_contradiction(memory, current)
    assert hint is None
