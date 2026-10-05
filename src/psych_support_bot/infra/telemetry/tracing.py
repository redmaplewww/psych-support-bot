import hashlib
import hmac
import logging
import re
from collections.abc import Callable
from contextlib import contextmanager
from time import perf_counter
from typing import TypedDict

from psych_support_bot.infra.config.settings import get_settings

logger = logging.getLogger(__name__)

# Deny by default: numeric scores, arbitrary nested metadata, identity and free
# text are not operational telemetry. Only these measured counters leave here.
_METRIC_KEYS = frozenset(
    {
        "elapsed_ms",
        "latency_ms",
        "prompt_tokens",
        "completion_tokens",
        "input",
        "output",
        "total",
        "input_cached",
        "fallback_used",
        "failed",
    }
)

_CONTENT_LIMIT = 4000
_REDACTIONS = (
    (re.compile(r"(?i)\b[\w.+-]+@[\w.-]+\.[a-z]{2,}\b"), "[已隐藏邮箱]"),
    (re.compile(r"(?<!\d)(?:\+?86[- ]?)?1[3-9]\d{9}(?!\d)"), "[已隐藏手机号]"),
    (re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)"), "[已隐藏证件号]"),
    (re.compile(r"(?<!\d)(?:\d{1,3}\.){3}\d{1,3}(?!\d)"), "[已隐藏IP地址]"),
    (re.compile(r"(?i)https?://\S+"), "[已隐藏链接]"),
    (
        re.compile(r"(?i)\b(api[_ -]?key|access[_ -]?token|password|secret)\s*[:=]\s*\S+"),
        "[已隐藏凭据]",
    ),
)


def metrics_only(*, data: object, **_) -> dict:
    if not isinstance(data, dict):
        return {}
    return {key: value for key, value in data.items() if key in _METRIC_KEYS and type(value) in (bool, int, float)}


def anonymize_text(value: str) -> str:
    """Best-effort local redaction before conversational text leaves the app.

    Free text can still identify a person through context, so the consent copy
    describes this as de-identification/pseudonymisation rather than promising
    irreversible anonymity.
    """
    cleaned = "".join(char for char in value if char in "\n\t" or ord(char) >= 32)
    for pattern, replacement in _REDACTIONS:
        cleaned = pattern.sub(replacement, cleaned)
    return cleaned[:_CONTENT_LIMIT]


def langfuse_mask(*, data: object, **_) -> object:
    """Deny arbitrary fields; permit redacted text and allowlisted metrics."""
    if isinstance(data, str):
        return anonymize_text(data)
    return metrics_only(data=data)


def telemetry_subject_id(kind: str, value: str) -> str:
    """Stable keyed pseudonym used solely for grouping and erasure lookup."""
    if kind not in {"user", "session"}:
        raise ValueError("Unsupported telemetry subject kind")
    settings = get_settings()
    secret = (settings.langfuse_pseudonym_key or settings.jwt_secret_key).encode()
    digest = hmac.new(secret, f"langfuse:{kind}:{value}".encode(), hashlib.sha256).hexdigest()[:32]
    return f"anon_{kind}_{digest}"


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
            environment=settings.langfuse_environment,
            timeout=30,
            mask=langfuse_mask,
            # The installed SDK supports this export filter. Do not forward
            # automatic third-party HTTP/LLM spans with their own payloads.
            should_export_span=lambda span: (
                getattr(getattr(span, "instrumentation_scope", None), "name", "") == "langfuse-sdk"
            ),
        )
        logger.info(
            "Langfuse client initialised → %s (env=%s)",
            settings.langfuse_host,
            settings.langfuse_environment,
        )
    except Exception:
        logger.warning("Failed to initialise Langfuse client")
        _langfuse_client = None
    return _langfuse_client


@contextmanager
def trace_span(
    name: str,
    *,
    input: object | None = None,
    content_input: str | None = None,
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

    from contextlib import nullcontext

    propagation = nullcontext()
    if user_id or session_id:
        from langfuse import propagate_attributes

        propagation = propagate_attributes(
            user_id=telemetry_subject_id("user", user_id) if user_id else None,
            session_id=telemetry_subject_id("session", session_id) if session_id else None,
        )
    with propagation:
        cm = client.start_as_current_observation(
            name=name,
            as_type=as_type,  # type: ignore[arg-type]
            input=(
                anonymize_text(content_input)
                if content_input is not None and get_settings().langfuse_content_analytics
                else None
            ),
            metadata=metrics_only(data=metadata),
        )
        started = perf_counter()
        obs = None
        try:
            obs = cm.__enter__()
            yield obs
        except Exception:
            # Never hand provider exceptions (which may echo prompts) to OTel.
            if obs is not None:
                obs.update(metadata={"failed": True})
            cm.__exit__(None, None, None)
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
        merged = metrics_only(data=metadata)
        merged.setdefault("elapsed_ms", round(elapsed_ms, 2))
        obs.update(metadata=merged)
    except Exception:
        logger.debug("Failed to record span elapsed_ms")


def update_span_output(obs, output: object, *, include_content: bool = False) -> None:
    """Update metrics, or explicitly approved redacted conversational output."""
    if obs is None:
        return
    try:
        if include_content and isinstance(output, str) and get_settings().langfuse_content_analytics:
            obs.update(output=anonymize_text(output))
        else:
            obs.update(metadata=metrics_only(data=output))
    except Exception:
        logger.debug("Failed to update Langfuse span output")


def update_span_usage(obs, usage: dict[str, int]) -> None:
    """Best-effort update of a generation span's token usage.

    Keys follow Langfuse's usage_details convention (input / output / total /
    input_cached) — input_cached powers the prefix-cache hit-rate analysis
    (Phase 0 baseline for the prompt-layering refactor)."""
    if obs is None:
        return
    try:
        obs.update(usage_details=metrics_only(data=usage))
    except Exception:
        logger.debug("Failed to update Langfuse span usage")


def flush_langfuse() -> None:
    """Flush pending traces to Langfuse. Call at app shutdown or end of request."""
    client = get_langfuse()
    if client is None:
        return
    try:
        client.flush()
    except Exception:
        logger.debug("Failed to flush Langfuse")
