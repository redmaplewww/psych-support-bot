from psych_support_bot.infra.config.settings import get_settings
from psych_support_bot.infra.llm.factory import build_chat_model


def test_settings_reads_openai_compatible_env(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv(
        "OPENAI_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"
    )
    monkeypatch.setenv("OPENAI_MODEL", "glm-5")
    get_settings.cache_clear()

    settings = get_settings()

    assert settings.openai_api_key == "test-key"
    assert (
        settings.openai_base_url == "https://dashscope.aliyuncs.com/compatible-mode/v1"
    )
    assert settings.openai_model == "glm-5"


def test_build_chat_model_uses_custom_base_url(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv(
        "OPENAI_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"
    )
    monkeypatch.setenv("OPENAI_MODEL", "glm-5")
    get_settings.cache_clear()

    model = build_chat_model()

    assert str(model.model_name) == "glm-5"
    assert "dashscope" in str(model.openai_api_base)
    assert "glm" not in str(model.openai_api_base)


def test_settings_reads_dashscope_aliases(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("OPENAI_BASE_URL", "")
    monkeypatch.setenv("OPENAI_MODEL", "")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "dashscope-key")
    monkeypatch.setenv(
        "DASHSCOPE_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"
    )
    monkeypatch.setenv("DASHSCOPE_MODEL", "glm-5")
    get_settings.cache_clear()

    settings = get_settings()

    assert settings.openai_api_key == "dashscope-key"
    assert (
        settings.openai_base_url == "https://dashscope.aliyuncs.com/compatible-mode/v1"
    )
    assert settings.openai_model == "glm-5"
