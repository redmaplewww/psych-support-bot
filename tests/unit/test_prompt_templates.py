from psych_support_bot.ai.prompts.templates import (
    build_consultation_synthesis_prompt,
    build_output_prompt,
    build_process_prompt,
)


def test_output_prompt_requires_three_part_visible_structure() -> None:
    prompt = build_output_prompt(
        mode="support",
        risk_level="low",
        user_message="我最近状态很乱",
    )

    assert "回应" in prompt
    assert "工作性假设" in prompt
    assert "下一问" in prompt
    assert "exactly one question" in prompt


def test_process_prompt_mentions_structure_and_gentle_challenge() -> None:
    prompt = build_process_prompt(
        interview_stage="hypothesis_testing",
        question_strategy="gentle_challenge",
        challenge_allowed=True,
        loop_hint="Test the absolute claim against exceptions.",
        expected_language="en",
    )

    assert "Reflection, Working hypothesis, Next question" in prompt
    assert "Gentle challenge is allowed" in prompt
    assert "Do not stack multiple questions" in prompt


def test_consultation_synthesis_prompt_requires_visible_labels() -> None:
    prompt = build_consultation_synthesis_prompt(
        mode="support",
        risk_level="low",
        memory_summary="",
        knowledge_context="",
        consultation_framework="- Agent A",
        consultation_opinions="[Agent A]\n观察: x\n形成: y\n下一步: z",
        user_message="我总觉得自己不行",
        interview_stage="hypothesis_testing",
        question_strategy="gentle_challenge",
        challenge_allowed=True,
        loop_hint="Test the user's conclusion against evidence and exceptions.",
    )

    assert "回应, 工作性假设, and 下一问" in prompt
    assert "The visible reply must use the labels 回应, 工作性假设, and 下一问" in prompt
