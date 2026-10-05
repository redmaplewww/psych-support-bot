"""LLM 语义风险兜底层测试：max 单向合并、只兜 low、fail-safe 到规则。"""

from typing import cast

import pytest

from psych_support_bot.ai.nodes import risk_classifier as rc_mod
from psych_support_bot.ai.nodes.risk_classifier import _merge_upgrade, classify_risk
from psych_support_bot.ai.safety import llm_classifier as llm_mod
from psych_support_bot.ai.safety.llm_classifier import SemanticRead
from psych_support_bot.ai.schemas.messages import GeneratedReply, RiskResult
from psych_support_bot.ai.schemas.state import GraphState


def _semantic(topics: list[str] | None = None, emotional: str = "") -> SemanticRead:
    return SemanticRead(topics=topics or [], emotional_state=emotional)


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
            "generated_reply": GeneratedReply(text="", style="support"),
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
    """规则判 low 的危机隐喻消息被 LLM 升级为 high + crisis 模式。

    「我只想消失」已入高危词表（2026-09-06 被动死亡意愿专项）直达
    high，不再依赖 LLM——本用例改用词表外隐喻验证 LLM 兜底通道本身。
    """
    calls: list[str] = []
    # patch 导入后的引用（risk_classifier 命名空间），而非源模块
    monkeypatch.setattr(
        rc_mod,
        "classify_risk_llm",
        lambda msg, lang: (calls.append(msg), (_llm("high"), _semantic()))[1],
    )
    state = classify_risk(_build_state("我想去一个没人找得到我的地方"))
    assert calls == ["我想去一个没人找得到我的地方"]
    assert state["risk_result"].risk_level == "high"
    assert state["risk_result"].needs_crisis_mode is True
    assert state["mode"] == "crisis"


def test_zh_passive_ideation_now_direct_high() -> None:
    """「想消失」类被动死亡意愿入词表后由规则层直达 high（不经 LLM）。"""
    state = classify_risk(_build_state("我只想消失，永远地消失"))
    assert state["risk_result"].risk_level in {"high", "critical"}
    assert state["risk_result"].needs_crisis_mode is True
    assert state["mode"] == "crisis"


def test_llm_topics_merge_into_state(monkeypatch) -> None:
    """LLM 语义 topics 并进 state topics（词表外表达可达）；emotional_state 落 state。"""
    monkeypatch.setattr(
        rc_mod,
        "classify_risk_llm",
        lambda msg, lang: (
            _llm("elevated"),
            _semantic(topics=["depression", "motivation"], emotional="低落且失去动力"),
        ),
    )
    state = _build_state("我心情一直很低落，什么都不想做")
    state["topics"] = ["motivation"]  # 关键词层先检出的
    result = classify_risk(state)
    assert "depression" in result["topics"]  # 语义通道补上的
    assert "motivation" in result["topics"]  # 原有关键词保留
    assert result["emotional_state"] == "低落且失去动力"
    assert result["llm_topics"] == ["depression", "motivation"]


def test_llm_semantic_failure_keeps_keyword_topics(monkeypatch) -> None:
    """LLM 挂掉时 topics/emotional_state 维持关键词层原样（fail-safe）。"""

    def _boom(msg: str, lang: str) -> tuple:
        raise RuntimeError("LLM unavailable")

    monkeypatch.setattr(rc_mod, "classify_risk_llm", _boom)
    state = _build_state("我心情一直很低落")
    state["topics"] = ["motivation"]
    result = classify_risk(state)
    assert result["topics"] == ["motivation"]
    assert result.get("emotional_state", "") == ""


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
        result, semantic = m.classify_risk_llm("测试", "zh")
    finally:
        m._invoke = original  # type: ignore[assignment]
    assert result.needs_crisis_mode is True
    # 新字段缺省容忍：JSON 没带 topics/emotional_state 时空值返回
    assert semantic.topics == []
    assert semantic.emotional_state == ""


def test_llm_elevated_cannot_arm_crisis_mode() -> None:
    """LLM 输出 elevated + needs_crisis_mode=true 时旗标被压回——否则拼出
    risk=elevated + mode=crisis 的中间态（危机框架话术但无热线资源，
    Langfuse 基线巡检 2026-09-06 实证）。需要危机干预就把等级抬到 high。"""
    monkeypatched = '{"risk_level": "elevated", "needs_crisis_mode": true, "reason": "x"}'
    import psych_support_bot.ai.safety.llm_classifier as m

    original = m._invoke
    m._invoke = lambda *a, **k: monkeypatched  # type: ignore[assignment]
    try:
        result, _ = m.classify_risk_llm("测试", "zh")
    finally:
        m._invoke = original  # type: ignore[assignment]
    assert result.risk_level == "elevated"
    assert result.needs_crisis_mode is False


def test_merge_never_arms_crisis_below_high() -> None:
    """合并阀门收口：升级合并结果的危机旗标只由等级决定。"""
    rule = RiskResult(risk_level="low", risk_types=[], needs_crisis_mode=False, reason="rule")
    llm = RiskResult(risk_level="elevated", risk_types=["distress"], needs_crisis_mode=True, reason="llm")
    merged = _merge_upgrade(rule, llm)
    assert merged.risk_level == "elevated"
    assert merged.needs_crisis_mode is False

    llm_high = RiskResult(risk_level="high", risk_types=["safety"], needs_crisis_mode=False, reason="llm")
    merged_high = _merge_upgrade(rule, llm_high)
    assert merged_high.risk_level == "high"
    assert merged_high.needs_crisis_mode is True


def test_llm_topics_closed_set_validation() -> None:
    """topics 闭集校验：枚举外的值丢弃，最多保留 3 个。"""
    monkeypatched = (
        '{"risk_level": "elevated", "needs_crisis_mode": false, "reason": "x", '
        '"topics": ["depression", "hallucinated_topic", "sleep", "anger"], '
        '"emotional_state": "低落且失去动力"}'
    )
    import psych_support_bot.ai.safety.llm_classifier as m

    original = m._invoke
    m._invoke = lambda *a, **k: monkeypatched  # type: ignore[assignment]
    try:
        _, semantic = m.classify_risk_llm("测试", "zh")
    finally:
        m._invoke = original  # type: ignore[assignment]
    assert semantic.topics == ["depression", "sleep", "anger"]
    assert semantic.emotional_state == "低落且失去动力"
