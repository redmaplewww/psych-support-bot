"""Smoke test: app chat model works against configured endpoint."""

from psych_support_bot.infra.config.settings import get_settings
from psych_support_bot.infra.llm.factory import build_chat_model

s = get_settings()
print("model:", s.openai_model)
print("base_url:", s.openai_base_url)

model = build_chat_model(temperature=0.0)
result = model.invoke("只回复两个字：收到")
print("reply:", str(result.content)[:200])
