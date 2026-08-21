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
    try:
        obs = cm.__enter__()
        yield obs
    except Exception as exc:
        cm.__exit__(type(exc), exc, exc.__traceback__)
        raise
    else:
        cm.__exit__(None, None, None)


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
