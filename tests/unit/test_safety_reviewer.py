"""Tests for safety_reviewer: challenge review, red-line patterns, and fallback."""

from typing import cast

from psych_support_bot.ai.nodes.safety_reviewer import (
    _detect_challenge,
    _detect_redline,
    _fallback_text,
    _sanitize_challenge,
    _sanitize_text,
    review_response,
)
from psych_support_bot.ai.schemas.messages import (
    GeneratedReply,
    RiskResult,
)
from psych_support_bot.ai.schemas.state import GraphState


def _build_state(
    *,
    reply_text: str,
    challenge_allowed: bool = False,
    user_message: str = "我最近很难受",
    risk_level: str = "low",
    needs_crisis_mode: bool = False,
    expected_language: str = "",
) -> GraphState:
    if not expected_language:
        expected_language = "zh" if any("\u4e00" <= c <= "\u9fff" for c in user_message) else "en"
    return cast(
        GraphState,
        {
            "user_id": "test-user",
            "session_id": "session-1",
            "user_message": user_message,
            "memory_summary": "",
            "knowledge_context": "",
            "mode": "support",
            "risk_result": RiskResult(
                risk_level=risk_level,
                risk_types=[],
                needs_crisis_mode=needs_crisis_mode,
                reason="test",
            ),
            "generated_reply": GeneratedReply(
                text=reply_text,
                style="support",
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
            "challenge_allowed": challenge_allowed,
            "loop_hint": "Start broad.",
            "exercise_history": [],
            "refusal_history": [],
            "expected_language": expected_language,
        },
    )


# --- Challenge detection ---


def test_detect_chinese_challenge() -> None:
    assert _detect_challenge("你确定你真的没事吗？") is True


def test_detect_english_challenge() -> None:
    assert _detect_challenge("Are you sure about that?") is True


def test_detect_chinese_why_question() -> None:
    assert _detect_challenge("你为什么不试试说出来呢？") is True


def test_detect_english_have_you_considered() -> None:
    assert _detect_challenge("Have you considered talking to someone?") is True


def test_no_challenge_in_supportive_text() -> None:
    assert _detect_challenge("我在这里陪你，我们一起慢慢来。") is False


def test_no_challenge_in_normal_english() -> None:
    assert _detect_challenge("I hear you, that sounds really difficult.") is False


# --- Sanitize challenge ---


def test_sanitize_removes_challenge_keeps_rest() -> None:
    text = "我听到你了。\n你确定你真的没事吗？\n我们一起想想办法。"
    sanitized, was_modified = _sanitize_challenge(text, expected_language="zh")
    assert was_modified is True
    assert "你确定" not in sanitized
    assert "我听到你了" in sanitized
    assert "我们一起想想办法" in sanitized


def test_sanitize_all_challenge_returns_empty() -> None:
    text = "你确定吗？\n你为什么不试试？"
    sanitized, was_modified = _sanitize_challenge(text, expected_language="zh")
    assert was_modified is True
    assert sanitized == ""


# --- Integration: review_response ---


def test_challenge_removed_when_not_allowed() -> None:
    """When challenge_allowed=False, challenge sentences should be removed."""
    reply = "我能理解你的感受。\n你确定你真的没事吗？\n慢慢来就好。"
    state = _build_state(reply_text=reply, challenge_allowed=False)
    result = review_response(state)
    assert "你确定" not in result["generated_reply"].text
    assert "我能理解你的感受" in result["generated_reply"].text


def test_challenge_kept_when_allowed() -> None:
    """When challenge_allowed=True, challenge sentences should be kept."""
    reply = "我听到你说的了。\n你确定你真的没事吗？\n我们可以再聊聊。"
    state = _build_state(reply_text=reply, challenge_allowed=True)
    result = review_response(state)
    assert "你确定" in result["generated_reply"].text


def test_all_challenge_replaced_with_fallback() -> None:
    """If the entire reply is challenge, fallback text should be used."""
    reply = "你确定吗？\n你为什么不改变呢？"
    state = _build_state(reply_text=reply, challenge_allowed=False)
    result = review_response(state)
    # Should be replaced with fallback
    assert "你确定" not in result["generated_reply"].text
    assert "你为什么" not in result["generated_reply"].text
    assert len(result["generated_reply"].text) > 0


def test_english_challenge_removed_when_not_allowed() -> None:
    """English challenge should also be removed."""
    reply = "I hear you.\nAre you sure about that?\nLet's take it slow."
    state = _build_state(reply_text=reply, challenge_allowed=False, user_message="I feel bad")
    result = review_response(state)
    assert "Are you sure" not in result["generated_reply"].text
    assert "I hear you" in result["generated_reply"].text


def test_no_false_positive_on_supportive_reply() -> None:
    """Supportive replies should not be flagged as challenge."""
    reply = "我在这里陪你，你不必着急，我们慢慢来。"
    state = _build_state(reply_text=reply, challenge_allowed=False)
    result = review_response(state)
    assert result["generated_reply"].text == reply


def test_diagnosis_takes_priority_over_challenge() -> None:
    """Diagnosis patterns should be handled first (existing priority)."""
    reply = "你患有抑郁症。\n你确定你没事吗？"
    state = _build_state(reply_text=reply, challenge_allowed=False)
    result = review_response(state)
    assert "你患有抑郁症" not in result["generated_reply"].text


# --- P0-1: Pathological attribution detection ---


def test_detect_chinese_pathological_attribution_brain_distort() -> None:
    assert _detect_redline("你的大脑在扭曲你的感知") is True


def test_detect_chinese_pathological_attribution_brain_deceive() -> None:
    assert _detect_redline("你的大脑在欺骗你") is True


def test_detect_chinese_pathological_attribution_false_alarm() -> None:
    assert _detect_redline("你的大脑在发出错误的警报") is True


def test_detect_english_pathological_attribution_brain_distort() -> None:
    assert _detect_redline("Your brain is distorting your perception.") is True


def test_detect_english_pathological_attribution_playing_tricks() -> None:
    assert _detect_redline("Your brain is playing tricks on you.") is True


def test_no_false_positive_on_normal_psychoeducation_zh() -> None:
    """Normal psychoeducation should not be flagged."""
    assert _detect_redline("焦虑会让我们对事情的感受更强烈") is False


def test_no_false_positive_on_normal_psychoeducation_en() -> None:
    """Normal psychoeducation should not be flagged."""
    assert _detect_redline("Anxiety can make sensations feel stronger.") is False


def test_no_false_positive_on_body_response_zh() -> None:
    """Explaining body's natural response is not pathological attribution."""
    assert _detect_redline("身体在面对威胁时的自然反应") is False


# --- P0-2: Subjective-experience denial detection ---


def test_detect_chinese_experience_denial_not_real() -> None:
    assert _detect_redline("你看到的东西不是真实的") is True


def test_detect_chinese_experience_denial_voices_not_real() -> None:
    assert _detect_redline("那些声音不是真的") is True


def test_detect_chinese_experience_denial_just_imagination() -> None:
    assert _detect_redline("那只是你的想象") is True


def test_detect_english_experience_denial_not_real() -> None:
    assert _detect_redline("What you see isn't real.") is True


def test_detect_english_experience_denial_just_imagination() -> None:
    assert _detect_redline("It's just your imagination.") is True


def test_no_false_positive_on_acknowledging_feeling_zh() -> None:
    """Acknowledging the user's feelings is not denial."""
    assert _detect_redline("你感受到的恐惧是可以理解的") is False


def test_no_false_positive_on_exploring_experience_zh() -> None:
    """Asking about experience is not denial."""
    assert _detect_redline("你提到的那些感受很重要") is False


def test_no_false_positive_on_subjective_reality_en() -> None:
    """Acknowledging subjective reality is not denial."""
    assert _detect_redline("These feelings are real to you, even if they don't match external reality.") is False


# --- P0-3: Over-pathologization label detection ---


def test_detect_chinese_over_pathologization_hallucination() -> None:
    assert _detect_redline("这是幻觉") is True


def test_detect_chinese_over_pathologization_delusion() -> None:
    assert _detect_redline("你描述的是妄想") is True


def test_detect_chinese_over_pathologization_psychotic_symptom() -> None:
    assert _detect_redline("这属于精神病性症状") is True


def test_detect_english_over_pathologization_hallucination() -> None:
    assert _detect_redline("This is a hallucination.") is True


def test_detect_english_over_pathologization_delusion() -> None:
    assert _detect_redline("What you're describing is a delusion.") is True


def test_no_false_positive_on_quoting_user_zh() -> None:
    """Quoting the user's words and asking is not labelling."""
    assert _detect_redline("你提到听到别人听不到的声音，这种情况多久了？") is False


def test_no_false_positive_on_normalization_zh() -> None:
    """Normalizing is not labelling."""
    assert _detect_redline("在压力下产生不寻常的体验是比较常见的") is False


def test_no_false_positive_on_normalization_en() -> None:
    """Normalizing is not labelling."""
    assert _detect_redline("It's quite common to have unusual experiences under stress.") is False


# --- P0-4: Transition phrase after truncation ---


def test_sanitize_inserts_transition_zh() -> None:
    """A transition phrase should be inserted at the first truncation point."""
    text = "我能理解你的感受。\n你的大脑在扭曲你的感知。\n我们一起想想办法。"
    sanitized, was_modified = _sanitize_text(text, expected_language="zh")
    assert was_modified is True
    assert "你的大脑在扭曲你的感知" not in sanitized
    assert "我能理解你的感受" in sanitized
    assert "我们一起想想办法" in sanitized
    assert "我听到你说的了" in sanitized


def test_sanitize_inserts_transition_en() -> None:
    """A transition phrase should be inserted at the first truncation point."""
    text = "I hear you.\nYour brain is distorting your perception.\nLet's work on this together."
    sanitized, was_modified = _sanitize_text(text, expected_language="en")
    assert was_modified is True
    assert "distorting your perception" not in sanitized
    assert "I hear you" in sanitized
    assert "work on this together" in sanitized
    assert "let's continue" in sanitized.lower()


def test_sanitize_inserts_crisis_transition_zh() -> None:
    """Crisis context should use the crisis transition phrase."""
    text = "我能理解你的感受。\n你看到的东西不是真实的。\n我们一起想想办法。"
    sanitized, was_modified = _sanitize_text(
        text, needs_crisis_mode=True, expected_language="zh"
    )
    assert was_modified is True
    assert "你看到的东西不是真实的" not in sanitized
    assert "我在这里陪着你" in sanitized


def test_sanitize_only_one_transition() -> None:
    """Only one transition phrase should be inserted, even with multiple violations."""
    text = "你的大脑在扭曲你的感知。\n这是幻觉。\n你看到的东西不是真实的。\n我们可以一起想办法。"
    sanitized, was_modified = _sanitize_text(text, expected_language="zh")
    assert was_modified is True
    # Should contain exactly one transition phrase
    assert sanitized.count("我听到你说的了") == 1
    assert "你的大脑在扭曲" not in sanitized
    assert "幻觉" not in sanitized
    assert "你看到的东西不是真实的" not in sanitized
    assert "我们可以一起想办法" in sanitized


def test_sanitize_all_redline_returns_empty() -> None:
    """If the entire reply is red-line violations, return empty for fallback."""
    text = "你的大脑在扭曲你的感知。\n这是幻觉。"
    sanitized, was_modified = _sanitize_text(text, expected_language="zh")
    assert was_modified is True
    assert sanitized == ""


# --- P0-5: Context-aware fallback ---


def test_fallback_normal_zh() -> None:
    """Non-crisis fallback should return grounding phrase."""
    result = _fallback_text("我很难受", "zh")
    assert "我在这里陪你" in result


def test_fallback_normal_en() -> None:
    """Non-crisis fallback should return grounding phrase."""
    result = _fallback_text("I feel bad", "en")
    assert "I am here with you" in result


def test_fallback_crisis_uses_crisis_template() -> None:
    """Crisis fallback should include hotline resources."""
    risk = RiskResult(
        risk_level="high",
        risk_types=["safety"],
        needs_crisis_mode=True,
        reason="test",
    )
    result = _fallback_text("我想自杀", "zh", risk_result=risk)
    assert "400-161-9995" in result


def test_fallback_crisis_en_uses_crisis_template() -> None:
    """Crisis fallback in English should include 988."""
    risk = RiskResult(
        risk_level="high",
        risk_types=["safety"],
        needs_crisis_mode=True,
        reason="test",
    )
    result = _fallback_text("I want to die", "en", risk_result=risk)
    assert "988" in result


def test_review_crisis_fallback_on_all_redline() -> None:
    """When all content is red-line in crisis mode, fallback should include hotline."""
    reply = "你的大脑在扭曲你的感知。\n这是幻觉。"
    state = _build_state(
        reply_text=reply,
        risk_level="high",
        needs_crisis_mode=True,
        user_message="我听到有人监视我",
    )
    result = review_response(state)
    assert "400-161-9995" in result["generated_reply"].text


def test_review_normal_fallback_on_all_redline() -> None:
    """When all content is red-line in normal mode, fallback should be grounding."""
    reply = "你的大脑在扭曲你的感知。\n这是幻觉。"
    state = _build_state(reply_text=reply, user_message="我最近不太舒服")
    result = review_response(state)
    assert "我在这里陪你" in result["generated_reply"].text


# --- Integration: red-line + transition in review_response ---


def test_review_redline_truncated_with_transition() -> None:
    """Red-line violation should be truncated with a transition phrase."""
    reply = "我能理解你的感受。\n你的大脑在欺骗你。\n我们可以一起想办法。"
    state = _build_state(reply_text=reply, user_message="我最近压力很大")
    result = review_response(state)
    assert "你的大脑在欺骗你" not in result["generated_reply"].text
    assert "我能理解你的感受" in result["generated_reply"].text
    assert "我们可以一起想办法" in result["generated_reply"].text
    assert "我听到你说的了" in result["generated_reply"].text


def test_review_english_redline_truncated_with_transition() -> None:
    """English red-line violation should be truncated with an English transition."""
    reply = "I hear you.\nYour brain is deceiving you.\nLet's work on this together."
    state = _build_state(reply_text=reply, user_message="I feel stressed")
    result = review_response(state)
    assert "deceiving you" not in result["generated_reply"].text
    assert "I hear you" in result["generated_reply"].text
    assert "let's continue" in result["generated_reply"].text.lower()


def test_review_mixed_diagnosis_and_pathologization() -> None:
    """Both diagnosis and pathological attribution should be caught in one pass."""
    reply = "你患有抑郁症。\n你的大脑在扭曲你的感知。\n我们一起想想办法。"
    state = _build_state(reply_text=reply, user_message="我很难受")
    result = review_response(state)
    assert "你患有抑郁症" not in result["generated_reply"].text
    assert "你的大脑在扭曲" not in result["generated_reply"].text
    assert "我们一起想想办法" in result["generated_reply"].text
