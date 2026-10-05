"""Consultation experts and synthesis must see the same verbatim history."""

from types import SimpleNamespace

import pytest

from psych_support_bot.infra.llm import generation


@pytest.mark.parametrize("streaming", [False, True])
def test_consultation_experts_and_synthesis_receive_history(monkeypatch, streaming) -> None:
    captured = []
    history = [
        {"role": "user", "content": "I am stressed about work."},
        {"role": "assistant", "content": "Which part feels hardest?"},
    ]

    class Model:
        def invoke(self, messages):
            captured.append(messages)
            return SimpleNamespace(content="I hear you.")

        def stream(self, messages):
            captured.append(messages)
            yield SimpleNamespace(content="I hear ")
            yield SimpleNamespace(content="you.")

    monkeypatch.setattr(generation, "build_chat_model", lambda **_: Model())
    monkeypatch.setattr(
        generation,
        "consultation_agents",
        lambda: [{"label": "A", "school": "S1", "focus": "F1"}, {"label": "B", "school": "S2", "focus": "F2"}],
    )
    chunks = []
    reply, opinions = generation.generate_multidisciplinary_consultation(
        user_message="Continue with the previous topic.",
        mode="intervention",
        risk_level="low",
        memory_summary="",
        knowledge_context="",
        consultation_framework="test",
        interview_stage="engagement",
        question_strategy="open",
        challenge_allowed=False,
        loop_hint="Listen.",
        expected_language="en",
        history=history,
        conversation_intent="follow_up",
        on_token=chunks.append if streaming else None,
    )
    assert reply == "I hear you."
    assert len(opinions) == 2
    assert len(captured) == 3  # two experts plus synthesis, without fallback
    for messages in captured:
        assert [m.type for m in messages] == ["system", "human", "ai", "human"]
        assert [m.content for m in messages[1:3]] == [h["content"] for h in history]
        assert messages[-1].content == "Continue with the previous topic."
    assert history == [
        {"role": "user", "content": "I am stressed about work."},
        {"role": "assistant", "content": "Which part feels hardest?"},
    ]
    assert chunks == (["I hear ", "you."] if streaming else [])
