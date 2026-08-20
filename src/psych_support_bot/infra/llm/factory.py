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
}
DEFAULT_TEMPERATURE = 0.4


def get_temperature_for_mode(mode: str) -> float:
    """Return the appropriate temperature for a conversation mode."""
    return MODE_TEMPERATURES.get(mode, DEFAULT_TEMPERATURE)


def build_chat_model(*, temperature: float = DEFAULT_TEMPERATURE) -> ChatOpenAI:
    settings = get_settings()
    key = SecretStr(settings.openai_api_key) if settings.openai_api_key else None
    return ChatOpenAI(
        model=settings.openai_model,
        api_key=key,
        base_url=settings.openai_base_url or None,
        temperature=temperature,
        default_headers={"api-key": settings.openai_api_key},
    )
