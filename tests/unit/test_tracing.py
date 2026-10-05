from psych_support_bot.infra.telemetry.tracing import timed_call, trace_event


def test_trace_event_has_metadata() -> None:
    event = trace_event("demo", {"x": 1})
    assert event["event"] == "demo"
    assert event["metadata"]["x"] == 1


def test_timed_call_returns_result_and_trace() -> None:
    result, trace = timed_call("calc", lambda: 42)
    assert result == 42
    assert trace["event"] == "calc"
    assert "elapsed_ms" in trace["metadata"]


def test_exported_trace_contains_only_redacted_conversation_content(monkeypatch):
    import json
    from types import SimpleNamespace

    import pytest

    from psych_support_bot.infra.telemetry import tracing

    captured = []

    class Context:
        def __enter__(self):
            return SimpleNamespace(update=lambda **kwargs: captured.append(kwargs))

        def __exit__(self, *args):
            captured.append({"exit": args})

    def start(**kwargs):
        captured.append(kwargs)
        return Context()

    class Propagation:
        def __init__(self, kwargs):
            self.kwargs = kwargs

        def __enter__(self):
            captured.append({"propagation": self.kwargs})

        def __exit__(self, *_):
            return None

    monkeypatch.setattr(tracing, "get_langfuse", lambda: SimpleNamespace(start_as_current_observation=start))
    monkeypatch.setattr("langfuse.propagate_attributes", lambda **kwargs: Propagation(kwargs))
    with (
        pytest.raises(RuntimeError),
        tracing.trace_span(
            "test.operation",
            input={"system_prompt": "INTERNAL_SYSTEM_PROMPT"},
            content_input="我叫小明，电话 13812345678，邮箱 me@example.com",
            metadata={"memory_summary": "SECRET_PROFILE", "score": 7, "latency_ms": 12},
            user_id="raw-user-id",
            session_id="raw-session-id",
        ) as obs,
    ):
        tracing.update_span_output(obs, "可以发邮件到 helper@example.com", include_content=True)
        tracing.update_span_usage(obs, {"input": 12, "output": 3})
        raise RuntimeError("SECRET_PROVIDER_EXCEPTION")
    serialized = json.dumps(captured, ensure_ascii=False)
    assert "INTERNAL_SYSTEM_PROMPT" not in serialized
    assert "SECRET_PROFILE" not in serialized
    assert "SECRET_PROVIDER_EXCEPTION" not in serialized
    assert "13812345678" not in serialized
    assert "me@example.com" not in serialized
    assert "helper@example.com" not in serialized
    assert "我叫小明" in serialized
    assert "raw-user-id" not in serialized
    assert "raw-session-id" not in serialized
    assert "anon_user_" in serialized
    assert "anon_session_" in serialized
    assert '"score"' not in serialized
    assert '"latency_ms": 12' in serialized
    assert '"input": 12' in serialized
    assert captured[-1] == {"exit": (None, None, None)}


def test_sdk_mask_redacts_text_and_drops_arbitrary_structured_fields():
    from psych_support_bot.infra.telemetry.tracing import langfuse_mask

    assert langfuse_mask(
        data={
            "message": "secret",
            "profile": {"name": "secret"},
            "mood_score": 8,
            "input": "raw message",
            "elapsed_ms": 5,
            "failed": True,
        }
    ) == {"elapsed_ms": 5, "failed": True}
    redacted = langfuse_mask(data="联系 13812345678 或 me@example.com，事件内容保留")
    assert "13812345678" not in redacted
    assert "me@example.com" not in redacted
    assert "事件内容保留" in redacted


def test_content_analytics_can_be_disabled_without_disabling_metrics(monkeypatch):
    from types import SimpleNamespace

    from psych_support_bot.infra.config.settings import get_settings
    from psych_support_bot.infra.telemetry import tracing

    captured = []

    class Context:
        def __enter__(self):
            return SimpleNamespace(update=lambda **kwargs: captured.append(kwargs))

        def __exit__(self, *_):
            return None

    monkeypatch.setenv("LANGFUSE_CONTENT_ANALYTICS", "false")
    get_settings.cache_clear()
    monkeypatch.setattr(
        tracing,
        "get_langfuse",
        lambda: SimpleNamespace(start_as_current_observation=lambda **kwargs: (captured.append(kwargs), Context())[1]),
    )
    with tracing.trace_span("disabled", content_input="private", metadata={"latency_ms": 7}) as obs:
        tracing.update_span_output(obs, "private reply", include_content=True)
    assert captured[0]["input"] is None
    assert captured[0]["metadata"] == {"latency_ms": 7}
    assert "private reply" not in str(captured)
