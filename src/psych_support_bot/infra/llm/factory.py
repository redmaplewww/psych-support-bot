from typing import NamedTuple

from langchain_openai import ChatOpenAI
from pydantic import SecretStr

from psych_support_bot.infra.config.settings import get_settings

# Temperature per conversation mode:
#   crisis     = 0.0  (deterministic, safety-critical)
#   assessment = 0.3  (low variance for consistent scoring guidance)
#   support    = 0.4  (natural but controlled empathy)
#   planning   = 0.4  (structured but flexible)
#   intervention = 0.5 (slightly creative for technique suggestions)
MODE_TEMPERATURES: dict[str, float] = {
    "crisis": 0.0,
    "assessment": 0.3,
    "support": 0.4,
    "planning": 0.4,
    "intervention": 0.5,
    # Risk classification must be deterministic and reproducible — a judgement
    # that flips with sampling noise directly changes crisis-mode routing.
    "risk_classification": 0.0,
}
DEFAULT_TEMPERATURE = 0.4


def get_temperature_for_mode(mode: str) -> float:
    """Return the appropriate temperature for a conversation mode."""
    return MODE_TEMPERATURES.get(mode, DEFAULT_TEMPERATURE)


class ModelCallLimits(NamedTuple):
    max_tokens: int
    timeout: float


# Per-mode ceilings (max_tokens) and timeouts (seconds). Output caps only clip
# pathological runs — normal replies are 3 short bubbles, far below the limit;
# classification outputs a small JSON. Timeouts bound the worst case: retrying
# is owned by the _invoke choke point, so a hung HTTP call fails fast into the
# caller's declared fallback instead of stacking langchain's default retries.
MODE_LIMITS: dict[str, "ModelCallLimits"] = {
    "crisis": ModelCallLimits(max_tokens=1024, timeout=30.0),
    "assessment": ModelCallLimits(max_tokens=1024, timeout=30.0),
    "support": ModelCallLimits(max_tokens=1024, timeout=30.0),
    "planning": ModelCallLimits(max_tokens=1024, timeout=30.0),
    "intervention": ModelCallLimits(max_tokens=1024, timeout=30.0),
    "risk_classification": ModelCallLimits(max_tokens=256, timeout=15.0),
}


def build_chat_model(
    *,
    temperature: float = DEFAULT_TEMPERATURE,
    mode: str = "support",
    max_tokens: int | None = None,
    timeout: float | None = None,
) -> ChatOpenAI:
    """Build a ChatOpenAI. Defaults (caps/timeout) come from the call mode;
    explicit arguments override — speculative/parallel callers may tighten them.

    max_retries=0: retry policy lives solely in _invoke (choke point) so the
    effective worst case is (1 + len(_RETRY_BACKOFF_SECONDS)) HTTP calls, not
    (1+2 langchain) × (1+2 choke point).
    """
    settings = get_settings()
    key = SecretStr(settings.openai_api_key) if settings.openai_api_key else None
    limits = MODE_LIMITS.get(mode)
    return ChatOpenAI(
        model=settings.openai_model,
        api_key=key,
        base_url=settings.openai_base_url or None,
        temperature=temperature,
        max_retries=0,
        timeout=timeout if timeout is not None else (limits.timeout if limits else 30.0),
        max_tokens=max_tokens if max_tokens is not None else (limits.max_tokens if limits else 1024),
        default_headers={"api-key": settings.openai_api_key},
    )
