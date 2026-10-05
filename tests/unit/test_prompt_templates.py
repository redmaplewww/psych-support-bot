from psych_support_bot.ai.prompts.templates import (
    build_consultation_synthesis_prompt,
    build_mode_shape_prompt,
    build_output_contract_prompt,
    build_process_state_prompt,
    build_static_prefix,
)


def test_mode_shape_support_allows_flexible_shape() -> None:
    """批次3 共情化：support 形态弹性化（1-3 条），单问契约在 output contract。"""
    shape = build_mode_shape_prompt("support", "low")

    assert "ONE to THREE short conversational messages" in shape
    assert "At most ONE question" in shape
    assert "must not appear in consecutive replies" in shape


def test_mode_shape_non_support_keeps_three_part_skeleton() -> None:
    """assessment/planning/intervention 保留三段式（采集与教学需要结构）。"""
    shape = build_mode_shape_prompt("assessment", "low")

    assert "EXACTLY three short conversational messages" in shape


def test_output_contract_carries_labels_and_single_question_rule() -> None:
    contract = build_output_contract_prompt("zh")

    assert "No labels, headings, numbering, or meta words like '回应', '工作性假设', '下一问'" in contract
    assert "Never stack multiple questions" in contract
    # 单问禁令的唯一机制性表述在 contract，mode shape 不重复
    assert "Do not stack multiple questions" not in contract


def test_process_state_mentions_gentle_challenge() -> None:
    state = build_process_state_prompt(
        interview_stage="hypothesis_testing",
        question_strategy="gentle_challenge",
        challenge_allowed=True,
        loop_hint="Test the absolute claim against exceptions.",
    )

    assert "Gentle challenge is allowed" in state
    assert "Current interview stage: hypothesis_testing" in state


def test_consultation_synthesis_prompt_requires_unlabeled_messages() -> None:
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

    assert "three short unlabeled conversational messages" in prompt
    assert "must use the labels" not in prompt
    # Phase 5 分层：静态前缀在最前，意见贴近尾部
    assert prompt.index("Identity policy") < prompt.index("Consultation opinions:")


def test_static_prefix_is_language_pooled_and_stable() -> None:
    zh = build_static_prefix("zh")
    en = build_static_prefix("en")

    assert zh != en
    assert "Language lock" in zh and "Language lock" in en
    # 静态前缀不含任何每轮插值
    assert "Conversation mode:" not in zh
    assert "Current assessed risk" not in zh


def test_mode_shape_quiet_mode_suppresses_questions() -> None:
    shape = build_mode_shape_prompt("support", "low", no_question_mode=True)

    assert "QUIET MODE OVERRIDE" in shape
    assert "Omit every question this turn" in shape
    assert "EXACTLY three short conversational messages" not in shape
