import time
from typing import cast

from psych_support_bot.ai.nodes.response_generator import generate_response
from psych_support_bot.ai.schemas.messages import (
    ConversationMode,
    GeneratedReply,
    RiskLevel,
    RiskResult,
)
from psych_support_bot.ai.schemas.state import GraphState
from psych_support_bot.infra.llm.generation import (
    _enforce_language,
    generate_multidisciplinary_consultation,
)


def _build_state(*, mode: ConversationMode, user_message: str, risk_level: RiskLevel = "low") -> GraphState:
    return cast(
        GraphState,
        {
            "user_id": "test-user",
            "session_id": "session-1",
            "user_message": user_message,
            "memory_summary": "",
            "knowledge_context": "",
            "mode": mode,
            "risk_result": RiskResult(
                risk_level=risk_level,
                risk_types=[],
                needs_crisis_mode=risk_level in {"high", "critical"},
                reason="test",
            ),
            "generated_reply": GeneratedReply(
                text="",
                style=mode,
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
            "loop_hint": "Start broad, reflect the main concern, then narrow only after the user gives specifics.",
            "expected_language": "zh" if any("\u4e00" <= c <= "\u9fff" for c in user_message) else "en",
        },
    )


def test_llm_failure_returns_template_fallback(monkeypatch) -> None:
    """P0-1: LLM failure should set fallback_used=True and return a safe template reply, not raise."""
    monkeypatch.setattr(
        "psych_support_bot.ai.nodes.response_generator.generate_clinically_bounded_reply",
        lambda **_: (_ for _ in ()).throw(RuntimeError("llm unavailable")),
    )
    state = _build_state(mode="support", user_message="I feel stressed and need support")

    result = generate_response(state)

    assert result["fallback_used"] is True
    assert result["generated_reply"].text
    assert "here with you" in result["generated_reply"].text.lower()


def test_crisis_mode_bypasses_llm(monkeypatch) -> None:
    called = {"llm": False}

    def _raise_if_called(**_: str) -> str:
        called["llm"] = True
        raise AssertionError("LLM should not be called in crisis mode")

    monkeypatch.setattr(
        "psych_support_bot.ai.nodes.response_generator.generate_clinically_bounded_reply",
        _raise_if_called,
    )
    state = _build_state(
        mode="crisis",
        user_message="I want to hurt myself tonight",
        risk_level="critical",
    )

    result = generate_response(state)

    assert called["llm"] is False
    assert "immediate danger" in result["generated_reply"].text


def test_consultation_mode_collects_all_agent_opinions(monkeypatch) -> None:
    monkeypatch.setattr(
        "psych_support_bot.ai.nodes.response_generator.generate_multidisciplinary_consultation",
        lambda **_: (
            "integrated consultation reply",
            [
                {"agent": "CBT Agent", "opinion": "a"},
                {"agent": "Psychodynamic Agent", "opinion": "b"},
                {"agent": "Humanistic Agent", "opinion": "c"},
                {"agent": "ACT Agent", "opinion": "d"},
                {"agent": "DBT Agent", "opinion": "e"},
            ],
        ),
    )
    state = _build_state(mode="intervention", user_message="请从不同流派会诊一下")
    state["consultation_required"] = True
    state["consultation_agents"] = [
        "CBT Agent",
        "Psychodynamic Agent",
        "Humanistic Agent",
        "ACT Agent",
        "DBT Agent",
    ]

    result = generate_response(state)

    assert result["generated_reply"].text == "integrated consultation reply"
    assert len(result["consultation_opinions"]) == 5
    assert result["consultation_notes"] == "5 agents consulted; stage=engagement; question=open"


def test_single_response_path_includes_process_metadata(monkeypatch) -> None:
    monkeypatch.setattr(
        "psych_support_bot.ai.nodes.response_generator.generate_clinically_bounded_reply",
        lambda **_: "supportive process reply",
    )
    state = _build_state(mode="support", user_message="我最近有点乱，不知道怎么说")
    state["interview_stage"] = "exploration"
    state["question_strategy"] = "open"

    result = generate_response(state)

    assert result["generated_reply"].text == "supportive process reply"
    assert result["consultation_notes"] == "single response path; stage=exploration; question=open"


def test_language_enforcement_rejects_english_for_chinese_user() -> None:
    try:
        _enforce_language("This is English output", "zh")
        raise AssertionError("expected ValueError")
    except ValueError as exc:
        assert "Language mismatch" in str(exc)


def test_multidisciplinary_consultation_runs_agents_concurrently(monkeypatch) -> None:
    monkeypatch.setattr(
        "psych_support_bot.infra.llm.generation.consultation_agents",
        lambda: (
            {"label": "A", "school": "S1", "focus": "F1"},
            {"label": "B", "school": "S2", "focus": "F2"},
            {"label": "C", "school": "S3", "focus": "F3"},
        ),
    )

    def _fake_generate_consultation_opinion(**kwargs):  # type: ignore[no-untyped-def]
        time.sleep(0.15)
        agent = kwargs["agent"]
        return {
            "agent": agent["label"],
            "school": agent["school"],
            "focus": agent["focus"],
            "opinion": f"opinion-{agent['label']}",
        }

    monkeypatch.setattr(
        "psych_support_bot.infra.llm.generation._generate_consultation_opinion",
        _fake_generate_consultation_opinion,
    )
    monkeypatch.setattr(
        "psych_support_bot.infra.llm.generation._invoke",
        lambda *args, **kwargs: "synthesized reply",
    )

    start = time.perf_counter()
    reply, opinions = generate_multidisciplinary_consultation(
        user_message="please consult",
        mode="intervention",
        risk_level="low",
        memory_summary="",
        knowledge_context="",
        consultation_framework="framework",
        interview_stage="pattern_analysis",
        question_strategy="looping",
        challenge_allowed=True,
        loop_hint="Track the sequence before concluding.",
    )
    elapsed = time.perf_counter() - start

    assert reply == "synthesized reply"
    assert [item["agent"] for item in opinions] == ["A", "B", "C"]
    assert elapsed < 0.35


# ---------------------------------------------------------------------------
# 句子级流式分支（stream_tokens=True 的普通 LLM 路径）
# ---------------------------------------------------------------------------


def test_stream_tokens_emits_chunks_and_fills_state(monkeypatch) -> None:
    """流式开关：token 经 writer 逐块推出，完整文本照常写入 state。"""
    events: list = []

    def fake_stream(**kwargs):
        yield "嗯，我听到了。"
        yield "慢慢来，我陪你。"

    monkeypatch.setattr(
        "psych_support_bot.ai.nodes.response_generator.generate_clinically_bounded_reply_stream_sync",
        fake_stream,
    )

    class _FakeWriter:
        def __call__(self, payload):
            events.append(payload)

    monkeypatch.setattr(
        "psych_support_bot.ai.nodes.response_generator.get_stream_writer",
        lambda: _FakeWriter(),
    )
    state = _build_state(mode="support", user_message="我心里有点乱")
    state["stream_tokens"] = True
    generate_response(state)
    assert events == [
        {"type": "token", "text": "嗯，我听到了。"},
        {"type": "token", "text": "慢慢来，我陪你。"},
    ]
    assert state["generated_reply"].text == "嗯，我听到了。慢慢来，我陪你。"


def test_speculative_adoption_streams_tokens_when_enabled(monkeypatch) -> None:
    """投机采纳轮也必须经 writer 吐 token：否则 respond_stream 无句事件，
    前端 live 字幕/句级朗读整体退化为 final 整段蹦出（2026-09-10 线上实证）。"""
    events: list = []

    class _W:
        def __call__(self, payload):
            events.append(payload)

    monkeypatch.setattr(
        "psych_support_bot.ai.nodes.response_generator.get_stream_writer",
        lambda: _W(),
    )
    # 若误走正常生成路径会打这个原语——放个炸断言钉死"未重生成"
    monkeypatch.setattr(
        "psych_support_bot.ai.nodes.response_generator.generate_clinically_bounded_reply",
        lambda **_: (_ for _ in ()).throw(AssertionError("speculative should skip regeneration")),
    )
    state = _build_state(mode="support", user_message="我心里有点乱")
    state["stream_tokens"] = True
    state["speculative_reply"] = "嗯，我听到了。慢慢来，我们一句一句说。"
    generate_response(state)
    assert events == [{"type": "token", "text": "嗯，我听到了。慢慢来，我们一句一句说。"}]
    assert state["generated_reply"].text == "嗯，我听到了。慢慢来，我们一句一句说。"
    assert state["speculative_reply"] is None


def test_speculative_adoption_skips_writer_when_not_streaming(monkeypatch) -> None:
    """非流式调用（普通 /respond、后台任务）不触碰 get_stream_writer（图外会抛）。"""

    def _boom():
        raise AssertionError("writer must not be touched when stream_tokens is off")

    monkeypatch.setattr("psych_support_bot.ai.nodes.response_generator.get_stream_writer", _boom)
    state = _build_state(mode="support", user_message="我心里有点乱")
    state["speculative_reply"] = "嗯，我听到了。"
    generate_response(state)
    assert state["generated_reply"].text == "嗯，我听到了。"


def test_stream_tokens_off_uses_plain_invoke(monkeypatch) -> None:
    """默认（非流式）路径行为零变化：不触发流式原语。"""
    stream_called = {"n": 0}

    def fake_stream(**_):
        stream_called["n"] += 1
        yield "x"

    monkeypatch.setattr(
        "psych_support_bot.ai.nodes.response_generator.generate_clinically_bounded_reply_stream_sync",
        fake_stream,
    )
    monkeypatch.setattr(
        "psych_support_bot.ai.nodes.response_generator.generate_clinically_bounded_reply",
        lambda **_: "整段回复。",
    )
    state = _build_state(mode="support", user_message="我有点累")
    generate_response(state)
    assert stream_called["n"] == 0
    assert state["generated_reply"].text == "整段回复。"
