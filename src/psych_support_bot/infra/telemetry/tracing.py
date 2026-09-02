import logging
from collections.abc import Callable
from contextlib import contextmanager
from time import perf_counter
from typing import TypedDict

from psych_support_bot.infra.config.settings import get_settings

logger = logging.getLogger(__name__)


class TraceEvent(TypedDict):
    event: str
    metadata: dict[str, object]
    host: str
    public_key: str
    configured: str


def tracing_config() -> dict[str, str]:
    settings = get_settings()
    return {
        "host": settings.langfuse_host,
        "public_key": settings.langfuse_public_key,
        "configured": str(bool(settings.langfuse_public_key and settings.langfuse_secret_key)).lower(),
    }


def trace_event(name: str, metadata: dict[str, object] | None = None) -> TraceEvent:
    return TraceEvent(
        event=name,
        metadata=metadata or {},
        **tracing_config(),
    )


def timed_call(name: str, callback: Callable[[], object]) -> tuple[object, TraceEvent]:
    started = perf_counter()
    result = callback()
    elapsed_ms = round((perf_counter() - started) * 1000, 2)
    return result, trace_event(name, {"elapsed_ms": elapsed_ms})


# ---------------------------------------------------------------------------
# Langfuse SDK integration (OpenTelemetry-based, no langchain dependency)
# ---------------------------------------------------------------------------

_langfuse_client = None


def get_langfuse():
    """Return a singleton Langfuse client, or None if not configured."""
    global _langfuse_client
    if _langfuse_client is not None:
        return _langfuse_client

    settings = get_settings()
    if not (settings.langfuse_public_key and settings.langfuse_secret_key):
        return None

    try:
        from langfuse import Langfuse

        _langfuse_client = Langfuse(
            public_key=settings.langfuse_public_key,
            secret_key=settings.langfuse_secret_key,
            host=settings.langfuse_host,
            timeout=30,
        )
        logger.info("Langfuse client initialised → %s", settings.langfuse_host)
    except Exception:
        logger.exception("Failed to initialise Langfuse client")
        _langfuse_client = None
    return _langfuse_client


@contextmanager
def trace_span(
    name: str,
    *,
    input: object | None = None,
    metadata: dict[str, object] | None = None,
    as_type: str = "span",
    session_id: str | None = None,
    user_id: str | None = None,
):
    """Context manager that creates a Langfuse observation span.

    Falls back to a no-op if Langfuse is not configured, so callers
    don't need to guard every site.
    """
    client = get_langfuse()
    if client is None:
        yield None
        return

    cm = client.start_as_current_observation(
        name=name,
        as_type=as_type,  # type: ignore[arg-type]
        input=input,
        metadata=metadata,
    )
    started = perf_counter()
    try:
        obs = cm.__enter__()
        _attach_trace_fields(obs, session_id=session_id, user_id=user_id)
        yield obs
    except Exception as exc:
        cm.__exit__(type(exc), exc, exc.__traceback__)
        raise
    else:
        _record_span_elapsed(obs, metadata, (perf_counter() - started) * 1000)
        cm.__exit__(None, None, None)


def _record_span_elapsed(obs, metadata: dict[str, object] | None, elapsed_ms: float) -> None:
    """Best-effort elapsed_ms onto span metadata — per-node/per-call latency
    is the basis for latency optimization analysis (see docs/technical)."""
    if obs is None:
        return
    try:
        merged = dict(metadata or {})
        merged.setdefault("elapsed_ms", round(elapsed_ms, 2))
        obs.update(metadata=merged)
    except Exception:
        logger.debug("Failed to record span elapsed_ms", exc_info=True)


def _attach_trace_fields(obs, *, session_id: str | None, user_id: str | None) -> None:
    """Best-effort mapping of session/user onto the parent trace so the UI
    groups conversations correctly.

    Langfuse v4 reads these from OTel span attributes (keys ``session.id``
    and ``user.id``); ``update_trace`` no longer exists on spans there.
    """
    if obs is None or not (session_id or user_id):
        return
    otel_span = getattr(obs, "_otel_span", None)
    is_recording = getattr(otel_span, "is_recording", None)
    if otel_span is None or not (callable(is_recording) and is_recording()):
        logger.debug("Langfuse span has no recording otel span; skipping trace fields")
        return
    try:
        if session_id:
            otel_span.set_attribute("session.id", session_id)
        if user_id:
            otel_span.set_attribute("user.id", user_id)
    except Exception:
        logger.debug("Failed to set Langfuse trace session/user attributes", exc_info=True)


def update_span_output(obs, output: object) -> None:
    """Best-effort update of a span's output."""
    if obs is None:
        return
    try:
        obs.update(output=output)
    except Exception:
        logger.debug("Failed to update Langfuse span output", exc_info=True)


def flush_langfuse() -> None:
    """Flush pending traces to Langfuse. Call at app shutdown or end of request."""
    client = get_langfuse()
    if client is None:
        return
    try:
        client.flush()
    except Exception:
        logger.debug("Failed to flush Langfuse", exc_info=True)
