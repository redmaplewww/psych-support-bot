"""LLM 语义风险兜底层测试：max 单向合并、只兜 low、fail-safe 到规则。"""

from typing import cast

import pytest

from psych_support_bot.ai.nodes import risk_classifier as rc_mod
from psych_support_bot.ai.nodes.risk_classifier import _merge_upgrade, classify_risk
from psych_support_bot.ai.safety import llm_classifier as llm_mod
from psych_support_bot.ai.schemas.messages import GeneratedReply, RiskResult
from psych_support_bot.ai.schemas.state import GraphState


def _build_state(user_message: str, **extra: object) -> GraphState:
    return cast(
        GraphState,
        {
            "user_id": "test-user",
            "session_id": "session-1",
            "user_message": user_message,
            "memory_summary": "",
            "knowledge_context": "",
            "mode": "support",
            "risk_result": RiskResult(risk_level="low", risk_types=[], needs_crisis_mode=False, reason=""),
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
            "loop_hint": "",
            "exercise_history": [],
            "refusal_history": [],
            "expected_language": "zh",
            "turn_count": 0,
            "safety_floor_risk_level": "",
            "no_question_mode": False,
            "last_bot_reply": "",
            **extra,
        },
    )


def _llm(level: str, crisis: bool | None = None) -> RiskResult:
    return RiskResult(
        risk_level=level,
        risk_types=["safety"] if level in {"high", "critical"} else (["distress"] if level == "elevated" else []),
        needs_crisis_mode=crisis if crisis is not None else level in {"high", "critical"},
        reason=f"[llm] {level}",
    )


# ---- _merge_upgrade 纯函数 ----


def test_merge_upgrades_low_to_llm_verdict() -> None:
    rule = RiskResult(risk_level="low", risk_types=[], needs_crisis_mode=False, reason="rule")
    merged = _merge_upgrade(rule, _llm("high"))
    assert merged.risk_level == "high"
    assert merged.needs_crisis_mode is True
    assert "safety" in merged.risk_types


def test_merge_never_downgrades_rule_verdict() -> None:
    """单向阀门：规则 high 时，LLM 即便判 low 也不能拉回。"""
    rule = RiskResult(risk_level="high", risk_types=["safety"], needs_crisis_mode=True, reason="rule")
    assert _merge_upgrade(rule, _llm("low")) is rule
    assert _merge_upgrade(rule, _llm("elevated")) is rule


# ---- 节点级行为 ----


def test_rule_low_message_triggers_llm_fallback(monkeypatch) -> None:
    """规则判 low 的危机隐喻消息被 LLM 升级为 high + crisis 模式。"""
    calls: list[str] = []
    # patch 导入后的引用（risk_classifier 命名空间），而非源模块
    monkeypatch.setattr(
        rc_mod,
        "classify_risk_llm",
        lambda msg, lang: (calls.append(msg), _llm("high"))[1],
    )
    state = classify_risk(_build_state("我只想消失，永远地消失"))
    assert calls == ["我只想消失，永远地消失"]
    assert state["risk_result"].risk_level == "high"
    assert state["risk_result"].needs_crisis_mode is True
    assert state["mode"] == "crisis"


def test_rule_high_does_not_call_llm(monkeypatch) -> None:
    """规则已判 high 的消息不再调 LLM（直通）。"""
    monkeypatch.setattr(
        rc_mod,
        "classify_risk_llm",
        lambda msg, lang: (_ for _ in ()).throw(AssertionError("LLM must not be called")),
    )
    state = classify_risk(_build_state("我想自杀"))
    assert state["risk_result"].risk_level == "high"
    assert state["mode"] == "crisis"


def test_llm_failure_keeps_rule_verdict(monkeypatch) -> None:
    """LLM 挂掉时 fail-safe 到规则判定（low），不放大也不抛出。"""
    def _boom(msg: str, lang: str) -> RiskResult:
        raise RuntimeError("LLM unavailable")

    monkeypatch.setattr(rc_mod, "classify_risk_llm", _boom)
    state = classify_risk(_build_state("今天有点累"))
    assert state["risk_result"].risk_level == "low"
    assert state["mode"] == "support"


def test_llm_parse_failure_raises_for_failsafe(monkeypatch) -> None:
    """LLM 返回不可解析输出时抛错（节点层捕获后维持规则）。"""
    monkeypatch.setattr(llm_mod, "_invoke", lambda *a, **k: "not json at all")
    with pytest.raises(ValueError):
        llm_mod.classify_risk_llm("测试消息", "zh")


def test_llm_output_forces_crisis_flag_consistency() -> None:
    """LLM 输出 high 但 needs_crisis_mode=false 时，以 safety 为准强制对齐。"""
    monkeypatched = '{"risk_level": "high", "needs_crisis_mode": false, "reason": "x"}'
    import psych_support_bot.ai.safety.llm_classifier as m

    original = m._invoke
    m._invoke = lambda *a, **k: monkeypatched  # type: ignore[assignment]
    try:
        result = m.classify_risk_llm("测试", "zh")
    finally:
        m._invoke = original  # type: ignore[assignment]
    assert result.needs_crisis_mode is True
