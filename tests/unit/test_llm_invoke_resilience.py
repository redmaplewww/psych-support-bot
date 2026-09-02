"""_invoke 咽喉层韧性测试：瞬态重试、声明式降级、语义化异常。

所有 LLM 调用都必须经过这一层处理可用性——新调用点默认安全，
而不是依赖每个调用点自觉包 try/except。
"""

from types import SimpleNamespace

import pytest

import psych_support_bot.infra.llm.generation as gen
from psych_support_bot.infra.llm.generation import LLMUnavailableError, _invoke


class _FakeAPIError(Exception):
    """模拟 openai APIStatusError：带 status_code 属性的客户端异常。"""

    def __init__(self, status_code: int) -> None:
        super().__init__(f"api error {status_code}")
        self.status_code = status_code


class _FlakyModel:
    def __init__(self, outcomes: list[object]) -> None:
        self._outcomes = list(outcomes)
        self.calls = 0

    def invoke(self, _messages: object) -> object:
        self.calls += 1
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


@pytest.fixture()
def _no_sleep(monkeypatch):
    sleeps: list[float] = []
    monkeypatch.setattr(gen.time, "sleep", lambda s: sleeps.append(s))
    return sleeps


@pytest.fixture()
def _stub_settings(monkeypatch):
    monkeypatch.setattr(
        gen,
        "get_settings",
        lambda: SimpleNamespace(openai_model="test-model"),
    )


def _stub_model(monkeypatch, outcomes: list[object]) -> _FlakyModel:
    model = _FlakyModel(outcomes)
    monkeypatch.setattr(gen, "build_chat_model", lambda **_: model)
    return model


def test_transient_429_retries_then_succeeds(monkeypatch, _no_sleep, _stub_settings) -> None:
    model = _stub_model(monkeypatch, [_FakeAPIError(429), SimpleNamespace(content="ok")])
    result = _invoke("sys", "user", "zh")
    assert result == "ok"
    assert model.calls == 2
    assert _no_sleep == [0.5]


def test_content_rejection_403_not_retried_serves_fallback(monkeypatch, _no_sleep, _stub_settings) -> None:
    model = _stub_model(monkeypatch, [_FakeAPIError(403)])
    result = _invoke("sys", "user", "zh", fallback=lambda: "确定性降级文本")
    assert result == "确定性降级文本"
    assert model.calls == 1  # 403 不可重试
    assert _no_sleep == []


def test_retries_exhausted_serves_fallback(monkeypatch, _no_sleep, _stub_settings) -> None:
    model = _stub_model(
        monkeypatch,
        [_FakeAPIError(429), _FakeAPIError(429), _FakeAPIError(429)],
    )
    result = _invoke("sys", "user", "en", fallback=lambda: "fallback reply")
    assert result == "fallback reply"
    assert model.calls == 3  # 原始 1 次 + 重试 2 次
    assert _no_sleep == [0.5, 1.0]


def test_retries_exhausted_without_fallback_raises_llm_unavailable(
    monkeypatch, _no_sleep, _stub_settings
) -> None:
    _stub_model(monkeypatch, [_FakeAPIError(500), _FakeAPIError(500), _FakeAPIError(500)])
    with pytest.raises(LLMUnavailableError):
        _invoke("sys", "user", "zh")
