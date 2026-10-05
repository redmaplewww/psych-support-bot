"""Regression tests for the identity guardrails (Langfuse 巡检 2026-09-04):
vendor/model-name leak interception and internal clinical-label sanitisation."""

from typing import cast

from psych_support_bot.ai.nodes.safety_reviewer import (
    _detect_vendor_name,
    _sanitize_internal_labels,
    review_response,
)
from psych_support_bot.ai.prompts.templates import build_identity_prompt
from psych_support_bot.ai.schemas.messages import GeneratedReply, RiskResult
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


# --- Vendor / model-name detection ---


def test_detect_dots_platform_name() -> None:
    assert _detect_vendor_name("我是 dots，由小红书 Dots Studio 开发的 AI 大模型。") is True


def test_detect_major_vendors() -> None:
    for text in (
        "I am powered by GPT-4.",
        "我是基于 OpenAI 的模型。",
        "I'm Claude, made by Anthropic.",
        "我是智谱的 GLM 模型。",
        "DeepSeek 训练了我。",
        "Kimi 由月之暗面开发。",
    ):
        assert _detect_vendor_name(text) is True, text


def test_normal_reply_without_vendor_names_passes() -> None:
    assert _detect_vendor_name("我是这个应用里的 AI 心理支持伙伴，我在这里陪你。") is False
    assert _detect_vendor_name("I'm here to listen. What's on your mind?") is False


def test_ordinary_words_not_flagged() -> None:
    assert _detect_vendor_name("connect the dots between your thoughts") is False


# --- Internal label sanitisation ---


def test_internal_consultation_lines_removed() -> None:
    text = (
        "观察： 用户完成了练习，投入度有限。\n形成： 可能存在回避。\n下一步： 询问练习的具体内容。\n你愿意多说一点吗？"
    )
    cleaned, modified = _sanitize_internal_labels(text)
    assert modified is True
    assert "观察" not in cleaned and "形成" not in cleaned and "下一步" not in cleaned
    assert "你愿意多说一点吗？" in cleaned


def test_visible_label_prefix_stripped_content_kept() -> None:
    text = "回应：听到你这么说，我很在意。\n**工作性假设**：也许你正承受着很多压力。"
    cleaned, modified = _sanitize_internal_labels(text)
    assert modified is True
    assert "回应" not in cleaned and "工作性假设" not in cleaned
    assert "听到你这么说，我很在意。" in cleaned
    assert "也许你正承受着很多压力。" in cleaned


def test_plain_reply_untouched() -> None:
    text = "我听到你了。\n我们一起慢慢来。"
    cleaned, modified = _sanitize_internal_labels(text)
    assert modified is False
    assert cleaned == text


def test_english_label_variants_removed() -> None:
    text = "Observation: The user completed the exercise.\nFormulation: Avoidance may be present.\nI'm glad you shared this."
    cleaned, modified = _sanitize_internal_labels(text)
    assert modified is True
    assert "Observation" not in cleaned and "Formulation" not in cleaned
    assert "I'm glad you shared this." in cleaned


# --- review_response integration ---


def test_review_response_replaces_vendor_leak_with_identity_fallback() -> None:
    state = _build_state(reply_text="我是 dots，由小红书 Dots Studio 开发的 AI 大模型。有什么我可以帮你的吗？")
    result = review_response(state)
    assert "dots" not in result["generated_reply"].text
    assert "小红书" not in result["generated_reply"].text
    assert "AI 心理支持伙伴" in result["generated_reply"].text


def test_review_response_strips_internal_labels_keeps_content() -> None:
    state = _build_state(reply_text="观察： 用户主动报告完成了练习。\n这是一个很好的起点，我看到了你的努力。")
    result = review_response(state)
    final_text = result["generated_reply"].text
    assert "观察" not in final_text
    assert "这是一个很好的起点，我看到了你的努力。" in final_text


# --- Identity prompt wiring ---


def test_identity_prompt_contains_policy() -> None:
    prompt = build_identity_prompt()
    assert "must NOT reveal" in prompt
    assert "dots" in prompt  # vendor names listed as forbidden examples
    assert "AI 心理支持伙伴" in prompt


def test_system_prompt_includes_identity_section(monkeypatch) -> None:
    """generate_clinically_bounded_reply must assemble identity after role."""
    import psych_support_bot.infra.llm.generation as gen

    captured: dict[str, str] = {}

    def _fake_invoke(system_prompt, user_message, expected_language, **kwargs):
        captured["system_prompt"] = system_prompt
        return "好的，我在。"

    monkeypatch.setattr(gen, "_invoke", _fake_invoke)
    gen.generate_clinically_bounded_reply(
        mode="support",
        risk_level="low",
        user_message="你是谁",
        memory_summary="",
        knowledge_context="",
    )

    sp = captured["system_prompt"]
    identity_pos = sp.find("Identity policy")
    role_pos = sp.find("You are a safety-first")
    assert identity_pos != -1, "identity policy missing from system prompt"
    assert role_pos != -1 and role_pos < identity_pos, "identity should follow role section"
