"""M2 首答延迟优化：风险 LLM 分类与 support 回复生成并行投机。

覆盖：
1. 投机命中：最终风险 ≤ elevated 且 support 模式 → response_generator 直接
   采用投机回复，不再调用自己的 LLM。
2. 投机丢弃：风险升级 high/critical（含跨轮升级/安全地板）→ 置 None，
   走危机路径。
3. 投机失败：risk LLM 成功但投机生成失败 → 降级正常串行生成。
4. 加温口径：投机参数固定 risk_level="elevated"（统一加温备注）。
5. 参数不污染：投机准备不得修改 state（丢弃后正常路径完整重算）。
"""

from typing import cast
from unittest.mock import patch

import pytest

from psych_support_bot.ai.nodes.response_generator import generate_response
from psych_support_bot.ai.nodes.risk_classifier import (
    _prepare_speculative_args,
    classify_risk,
)
from psych_support_bot.ai.schemas.messages import GeneratedReply, RiskResult
from psych_support_bot.ai.schemas.state import GraphState
from psych_support_bot.infra.config.settings import get_settings


def _build_state(
    *,
    user_message: str = "今天有点累，想聊聊",
    memory_summary: str = "",
    safety_floor: str = "",
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
            "mode": "support",
            "risk_result": RiskResult(
                risk_level=risk_level,
                risk_types=[],
                needs_crisis_mode=risk_level in {"high", "critical"},
                reason="test",
            ),
            "generated_reply": GeneratedReply(text="", style="support", includes_action_step=True),
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
            "expected_language": "zh",
            "turn_count": 3,
            "safety_floor_risk_level": safety_floor,
            "no_question_mode": False,
            "last_bot_reply": "",
            "refusal_history": [],
            "speculative_reply": None,
        },
    )


def _enable_speculation(monkeypatch) -> None:
    get_settings.cache_clear()
    monkeypatch.setenv("SPECULATIVE_REPLY_ENABLED", "true")
    get_settings.cache_clear()
    monkeypatch.delenv("SPECULATIVE_REPLY_ENABLED", raising=False)


# --- 投机参数准备 ---


def test_prepare_args_skipped_when_disabled(monkeypatch) -> None:
    get_settings.cache_clear()
    monkeypatch.setenv("SPECULATIVE_REPLY_ENABLED", "false")
    get_settings.cache_clear()
    assert _prepare_speculative_args(_build_state()) is None


def test_prepare_args_skipped_for_consultation_trigger() -> None:
    """诊断类关键词触发会诊（support 模式也触发）→ 投机回复必被丢弃，直接不投机。"""
    get_settings.cache_clear()
    state = _build_state(user_message="我是不是得了抑郁症？")
    assert _prepare_speculative_args(state) is None


def test_prepare_args_uses_elevated_and_does_not_mutate_state() -> None:
    """投机口径固定 elevated（加温备注）；且不得修改 state 的任何键。"""
    get_settings.cache_clear()
    with pytest.MonkeyPatch.context() as mp:
        _enable_speculation(mp)
        state = _build_state(user_message="最近工作压力很大，睡不好")
        before = {k: repr(v) for k, v in state.items()}
        args = _prepare_speculative_args(state)
        assert args is not None
        after = {k: repr(v) for k, v in state.items()}
        assert before == after  # 未污染 state
        assert args["interview_stage"]
        assert "knowledge_context" in args
    get_settings.cache_clear()


# --- 节点级裁决 ---


def test_speculative_adopted_when_risk_stays_low(monkeypatch) -> None:
    """规则 low + LLM low + 无地板 → 投机回复被采用。"""
    _enable_speculation(monkeypatch)
    state = _build_state()
    fake_spec = "投机回复：听起来今天很辛苦。"

    with (
        patch(
            "psych_support_bot.ai.nodes.risk_classifier.classify_risk_llm",
            return_value=RiskResult(risk_level="low", risk_types=[], needs_crisis_mode=False, reason="[llm] ok"),
        ),
        patch(
            "psych_support_bot.ai.nodes.risk_classifier._generate_speculative_reply",
            return_value=fake_spec,
        ),
    ):
        result = classify_risk(state)

    assert result["speculative_reply"] == fake_spec
    assert result["risk_result"].risk_level == "low"


def test_speculative_discarded_when_llm_upgrades_to_high(monkeypatch) -> None:
    """隐喻危机消息：规则 low → LLM 升级 high → 投机回复必须丢弃。"""
    _enable_speculation(monkeypatch)
    state = _build_state(user_message="我真想消失，永远地消失")

    with (
        patch(
            "psych_support_bot.ai.nodes.risk_classifier.classify_risk_llm",
            return_value=RiskResult(
                risk_level="high", risk_types=["safety"], needs_crisis_mode=True, reason="[llm] 隐喻死亡意愿"
            ),
        ),
        patch(
            "psych_support_bot.ai.nodes.risk_classifier._generate_speculative_reply",
            return_value="投机回复（必须丢弃）",
        ),
    ):
        result = classify_risk(state)

    assert result["speculative_reply"] is None
    assert result["risk_result"].risk_level == "high"
    assert result["mode"] == "crisis"


def test_speculative_discarded_by_cross_turn_upgrade(monkeypatch) -> None:
    """连续 elevated 跨轮升级为 high → 投机回复丢弃。"""
    _enable_speculation(monkeypatch)
    state = _build_state(
        user_message="我还是觉得没意义，撑不住了",
        memory_summary="User (mode=support, risk=elevated): 我觉得很绝望，没有希望",
    )

    with (
        patch(
            "psych_support_bot.ai.nodes.risk_classifier.classify_risk_llm",
            return_value=RiskResult(
                risk_level="elevated", risk_types=["distress"], needs_crisis_mode=False, reason="[llm] ok"
            ),
        ),
        patch(
            "psych_support_bot.ai.nodes.risk_classifier._generate_speculative_reply",
            return_value="投机回复（必须丢弃）",
        ),
    ):
        result = classify_risk(state)

    assert result["speculative_reply"] is None
    assert result["risk_result"].risk_level == "high"


def test_speculative_discarded_by_safety_floor(monkeypatch) -> None:
    """近期筛查安全地板把 low 抬到 elevated/high → 投机回复丢弃。"""
    _enable_speculation(monkeypatch)
    state = _build_state(safety_floor="high")

    with (
        patch(
            "psych_support_bot.ai.nodes.risk_classifier.classify_risk_llm",
            return_value=RiskResult(risk_level="low", risk_types=[], needs_crisis_mode=False, reason="[llm] ok"),
        ),
        patch(
            "psych_support_bot.ai.nodes.risk_classifier._generate_speculative_reply",
            return_value="投机回复（必须丢弃）",
        ),
    ):
        result = classify_risk(state)

    assert result["speculative_reply"] is None


def test_risk_llm_down_still_speculates(monkeypatch) -> None:
    """风险 LLM 不可用时维持规则判定；投机回复仍可被采用（规则判 low 可信）。"""
    _enable_speculation(monkeypatch)
    state = _build_state()

    with (
        patch(
            "psych_support_bot.ai.nodes.risk_classifier.classify_risk_llm",
            side_effect=RuntimeError("LLM down"),
        ),
        patch(
            "psych_support_bot.ai.nodes.risk_classifier._generate_speculative_reply",
            return_value="投机回复：我听到了。",
        ),
    ):
        result = classify_risk(state)

    assert result["risk_result"].risk_level == "low"
    assert result["speculative_reply"] == "投机回复：我听到了。"


def test_speculative_failure_falls_back_to_serial(monkeypatch) -> None:
    """投机生成失败 → speculative_reply=None，response_generator 走正常串行生成。"""
    _enable_speculation(monkeypatch)
    state = _build_state()

    with (
        patch(
            "psych_support_bot.ai.nodes.risk_classifier.classify_risk_llm",
            return_value=RiskResult(risk_level="low", risk_types=[], needs_crisis_mode=False, reason="[llm] ok"),
        ),
        patch(
            "psych_support_bot.ai.nodes.risk_classifier._generate_speculative_reply",
            side_effect=RuntimeError("generation failed"),
        ),
    ):
        result = classify_risk(state)

    assert result["speculative_reply"] is None


# --- response_generator 短路 ---


def test_response_generator_uses_speculative_reply(monkeypatch) -> None:
    state = _build_state()
    state["speculative_reply"] = "投机回复：听起来很不容易。"

    def _fail(*_args, **_kwargs) -> str:
        raise AssertionError("serial LLM generation must not run when speculative reply is adopted")

    monkeypatch.setattr(
        "psych_support_bot.ai.nodes.response_generator.generate_clinically_bounded_reply",
        _fail,
    )
    result = generate_response(state)
    assert result["generated_reply"].text == "投机回复：听起来很不容易。"
    assert result["speculative_reply"] is None  # 消费后置 None
    assert result["fallback_used"] is False


def test_response_generator_ignores_speculative_after_upgrade(monkeypatch) -> None:
    """投机回复残留但风险已升级（crisis 模式）→ 必须走危机路径。"""
    state = _build_state(user_message="我想结束这一切", risk_level="critical")
    state["mode"] = "crisis"
    state["speculative_reply"] = "投机回复（残留，必须忽略）"

    result = generate_response(state)
    # critical 走纯模板：含热线/急救资源，不含投机文本
    assert result["generated_reply"].text != "投机回复（残留，必须忽略）"
    assert "120" in result["generated_reply"].text or "热线" in result["generated_reply"].text


def test_response_generator_discards_speculative_verbatim_repeat(monkeypatch) -> None:
    """投机回复与上一轮逐字相同 → 丢弃并回退正常生成，不交付复读。

    复现 Langfuse 2026-09-02 c4fd09cc：第 3 轮投机输出 = 第 2 轮回复逐字拷贝。
    """
    canned = "听到你正在经历这样的事情，我感到非常难过和担心。这不是你的错。"
    state = _build_state()
    state["speculative_reply"] = canned
    state["last_bot_reply"] = canned
    # 正常生成路径 mock 成全新内容，验证投机被丢弃后走了生成
    monkeypatch.setattr(
        "psych_support_bot.ai.nodes.response_generator.generate_clinically_bounded_reply",
        lambda **_: "全新生成：关于你想了解的资源，我们可以慢慢梳理。",
    )
    result = generate_response(state)
    assert result["generated_reply"].text != canned
    assert result["generated_reply"].text.startswith("全新生成")
    assert result["speculative_reply"] is None


def test_speculative_args_include_anti_repeat_note(monkeypatch) -> None:
    """上一轮有回复时，投机 prompt 的 loop_hint 必须携带防复读指令。"""
    from psych_support_bot.ai.nodes.risk_classifier import _prepare_speculative_args

    _enable_speculation(monkeypatch)
    state = _build_state()
    state["last_bot_reply"] = "上一轮回复内容，非空即触发。"
    args = _prepare_speculative_args(state)
    assert args is not None
    assert "Anti-repeat guard" in args["loop_hint"]
    # 防复读指令不摘录上一轮原文（摘录会加重照抄倾向）
    assert "上一轮回复内容" not in args["loop_hint"]
