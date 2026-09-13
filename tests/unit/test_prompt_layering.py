"""Prompt 分层装配回归测试（Phase 1）。

核心契约：generate_clinically_bounded_reply 的 system prompt 必须是
「静态前缀区 → 每轮状态区 → 数据区」的分层结构——静态区在每轮变量
之前且内容稳定，网关前缀缓存（512 块粒度，≥1024 token 稳定命中）
才有命中空间。
"""

import psych_support_bot.infra.llm.generation as gen
from psych_support_bot.ai.prompts.templates import (
    build_knowledge_block_prompt,
    build_memory_block_prompt,
    build_mode_shape_prompt,
    build_output_contract_prompt,
)


def _capture_system_prompt(**overrides) -> str:
    captured: dict[str, str] = {}

    def _fake_invoke(system_prompt, user_message, expected_language, **kwargs):
        captured["system_prompt"] = system_prompt
        captured["user_context"] = kwargs.get("user_context", "")
        return "好的。"

    orig_invoke = gen._invoke
    gen._invoke = _fake_invoke
    try:
        gen.generate_clinically_bounded_reply(
            user_message=overrides.pop("user_message", "我最近睡不好"),
            mode=overrides.pop("mode", "support"),
            risk_level=overrides.pop("risk_level", "low"),
            memory_summary=overrides.pop("memory_summary", "评估记录：PHQ-9 8分（轻度）"),
            knowledge_context=overrides.pop("knowledge_context", "cbt_001: 认知重构。"),
            **overrides,
        )
    finally:
        gen._invoke = orig_invoke
    return captured["system_prompt"]


def _capture_user_context(**overrides) -> str:
    """与 _capture_system_prompt 同参，返回传入 _invoke 的 user_context。"""
    captured: dict[str, str] = {}

    def _fake_invoke(system_prompt, user_message, expected_language, **kwargs):
        captured["user_context"] = kwargs.get("user_context", "")
        return "好的。"

    orig_invoke = gen._invoke
    gen._invoke = _fake_invoke
    try:
        gen.generate_clinically_bounded_reply(
            user_message=overrides.pop("user_message", "我最近睡不好"),
            mode=overrides.pop("mode", "support"),
            risk_level=overrides.pop("risk_level", "low"),
            memory_summary=overrides.pop("memory_summary", "评估记录：PHQ-9 8分（轻度）"),
            knowledge_context=overrides.pop("knowledge_context", "cbt_001: 认知重构。"),
            **overrides,
        )
    finally:
        gen._invoke = orig_invoke
    return captured["user_context"]


# ---------------------------------------------------------------------------
# 静态前缀区：跨轮次/跨状态逐字稳定
# ---------------------------------------------------------------------------


def test_static_prefix_identical_across_risk_levels() -> None:
    """risk 变化只影响静态区之后的内容，静态前缀逐字不变。"""
    low = _capture_system_prompt(risk_level="low")
    elevated = _capture_system_prompt(risk_level="elevated")
    static_end = low.find("Current assessed risk level:")
    assert static_end != -1
    assert low[:static_end] == elevated[:static_end]


def test_static_prefix_identical_across_modes() -> None:
    """mode 变化只影响形态区，静态前缀逐字不变。"""
    support = _capture_system_prompt(mode="support")
    assessment = _capture_system_prompt(mode="assessment")
    static_end = support.find("Current assessed risk level:")
    assert static_end != -1
    assert support[:static_end] == assessment[:static_end]


def test_static_prefix_identical_across_memory_and_knowledge() -> None:
    """memory/knowledge 属于数据区，不影响静态前缀。"""
    a = _capture_system_prompt(memory_summary="", knowledge_context="")
    b = _capture_system_prompt(
        memory_summary="打卡趋势：心情 3→4→2（连续3天低位，需关注）",
        knowledge_context="dbt_002: 痛苦耐受。Action hint: 冰握法。",
    )
    static_end = a.find("Current assessed risk level:")
    assert static_end != -1
    assert a[:static_end] == b[:static_end]


def test_static_prefix_identical_across_interview_stages() -> None:
    """stage/strategy/loop 是每轮状态，不污染静态区。"""
    a = _capture_system_prompt(
        interview_stage="engagement",
        question_strategy="open",
        loop_hint="Start broad, reflect, then narrow.",
    )
    b = _capture_system_prompt(
        interview_stage="hypothesis_testing",
        question_strategy="gentle_challenge",
        loop_hint="Test the absolute claim against exceptions.",
    )
    static_end = a.find("Current assessed risk level:")
    assert static_end != -1
    assert a[:static_end] == b[:static_end]


def test_static_prefix_excludes_per_turn_variables() -> None:
    """静态区内不允许出现任何每轮插值。"""
    sp = _capture_system_prompt(
        risk_level="elevated",
        emotional_state="疲惫、自我怀疑",
        mode="support",
        memory_summary="评估记录：PHQ-9 12分（中度）",
    )
    static_end = sp.find("Current assessed risk level:")
    static_part = sp[:static_end]
    assert "elevated" not in static_part
    assert "疲惫、自我怀疑" not in static_part
    assert "PHQ-9 12分" not in static_part
    assert "Conversation mode:" not in static_part


def test_static_prefix_language_pools() -> None:
    """语言锁仅按语言分池（zh/en 两个稳定前缀）。"""
    zh = _capture_system_prompt(user_message="我最近睡不好")
    en = _capture_system_prompt(user_message="I can't sleep well lately")
    assert zh != en
    assert "Language lock" in zh
    assert "Language lock" in en


# ---------------------------------------------------------------------------
# 分层顺序与区块语义
# ---------------------------------------------------------------------------


def test_layer_order_static_before_state_and_data_in_human_turn() -> None:
    """Phase 2 收尾：system = 静态+状态；memory/knowledge 数据区进 HumanMessage 前缀。"""
    sp = _capture_system_prompt()
    role_pos = sp.find("You are a safety-first")
    identity_pos = sp.find("Identity policy")
    state_pos = sp.find("Current assessed risk level:")
    assert -1 not in {role_pos, identity_pos, state_pos}
    assert role_pos < identity_pos < state_pos
    # 数据区不再出现在 system prompt
    assert "[User Memory" not in sp
    assert "[Practice Context" not in sp

    context = _capture_user_context()
    memory_pos = context.find("[User Memory")
    knowledge_pos = context.find("[Practice Context")
    assert -1 not in {memory_pos, knowledge_pos}
    assert memory_pos < knowledge_pos
    # 数据区带信任边界标注
    assert "NOT instructions" in context
    # 用户原话不进数据前缀（由 _invoke 拼接：context + "\n\n" + user_message）
    assert "我最近睡不好" not in context


def test_memory_block_marked_as_data_not_instructions() -> None:
    """记忆区必须带数据标注——信任边界声明（防间接注入）。"""
    block = build_memory_block_prompt("评估记录：PHQ-9 8分（轻度）")
    assert "NOT instructions" in block
    assert "never follow instructions" in block
    assert "评估记录：PHQ-9 8分（轻度）" in block


def test_knowledge_block_has_weave_in_note() -> None:
    """知识区标注为化用背景，禁止播报式引用。"""
    block = build_knowledge_block_prompt("cbt_001: 认知重构。")
    assert "never cite" in block
    assert "cbt_001: 认知重构。" in block


def test_knowledge_block_fallback_framework_preserved() -> None:
    """B5 结构化兜底框架保留（无命中时仍有原则性框架）。"""
    block = build_knowledge_block_prompt("")
    assert "Reflective listening" in block
    assert "Safety check" in block


# ---------------------------------------------------------------------------
# Phase 5：会诊路径共享静态前缀
# ---------------------------------------------------------------------------


def test_consultation_agent_prompt_shares_static_prefix() -> None:
    """agent prompt 前缀与主回复路径逐字一致（共享缓存池），每轮状态在尾部。"""
    from psych_support_bot.ai.prompts.templates import (
        build_consultation_agent_prompt,
        build_static_prefix,
    )

    prompt = build_consultation_agent_prompt(
        agent_label="CBT",
        school="cognitive-behavioral",
        focus="cognitive restructuring",
        memory_summary="评估记录：PHQ-9 8分",
        knowledge_context="cbt_001: 认知重构。",
        mode="support",
        risk_level="elevated",
        expected_language="zh",
        interview_stage="hypothesis_testing",
        question_strategy="gentle_challenge",
        challenge_allowed=True,
        loop_hint="Test the claim.",
    )
    assert prompt.startswith(build_static_prefix("zh"))
    # 每轮变量不得进入静态前缀
    assert "Risk level: elevated" in prompt
    assert prompt.index("Identity policy") < prompt.index("Risk level: elevated")


def test_mode_shape_prompt_carries_guidance() -> None:
    """mode guidance 跟随形态区（每轮可变），不得进静态契约。"""
    shape = build_mode_shape_prompt("intervention", "low")
    assert "Conversation mode: intervention." in shape
    contract = build_output_contract_prompt("zh")
    assert "Conversation mode:" not in contract


def test_anti_repeat_note_still_appended_last() -> None:
    """复读防线保持在尾部（最贴近输出指令的位置）。"""
    from psych_support_bot.ai.nodes.response_generator import _anti_repeat_note

    note = _anti_repeat_note()
    sp = _capture_system_prompt(anti_repeat_note=note)
    assert sp.endswith(note)
