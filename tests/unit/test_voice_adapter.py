"""语音 I/O 适配层单测。

覆盖：配置解析（openai/dots/未配置/回落）、SSRF 防护、openai/dots STT
调用（mock httpx）、TTS 调用与未配置 503 语义。不发真实网络请求。

凭据值经 _fake_key() 现场生成（非硬编码），仅用于断言配置透传。
"""

import base64
import secrets
import time

import httpx
import pytest

from psych_support_bot.infra.config.settings import get_settings
from psych_support_bot.infra.voice.adapter import (
    VoiceNotConfigured,
    VoiceProviderError,
    get_stt_config,
    get_tts_config,
    synthesize,
    synthesize_stream,
    transcribe,
    validate_public_http_url,
)


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    from psych_support_bot.infra.voice import adapter as _voice_adapter

    _voice_adapter._reset_tts_cache_for_tests()
    get_settings.cache_clear()
    yield
    _voice_adapter._reset_tts_cache_for_tests()
    get_settings.cache_clear()


def _fake_key(prefix: str = "test") -> str:
    """现场生成假凭据（单测专用，无真实密钥）。"""
    return f"{prefix}-{secrets.token_hex(8)}"


@pytest.fixture()
def stt_key() -> str:
    return _fake_key("stt")


@pytest.fixture()
def tts_key() -> str:
    return _fake_key("tts")


@pytest.fixture()
def dots_key() -> str:
    return _fake_key("dots")


def _configure(stt_key: str = "", tts_key: str = "", **overrides) -> None:
    """直接改环境变量（get_settings 已被 cache_clear，重新构造生效）。"""
    import os

    defaults = {
        "VOICE_STT_PROVIDER": "openai",
        "VOICE_STT_BASE_URL": "https://stt.example.com/v1",
        "VOICE_STT_API_KEY": stt_key,
        "VOICE_STT_MODEL": "whisper-1",
        "VOICE_STT_LANGUAGE": "zh",  # 显式钉住：不依赖开发机 .env 是否带此项
        # 显式钉住 provider：开发机 .env 可能带真实 minimax 配置，
        # 不钉会把 openai 用例的 mock 请求路由到 WS 路径。
        "VOICE_TTS_PROVIDER": "openai",
        "VOICE_TTS_BASE_URL": "https://tts.example.com/v1",
        "VOICE_TTS_API_KEY": tts_key,
        "VOICE_TTS_MODEL": "tts-1",
        "VOICE_TTS_VOICE": "alloy",
        "OPENAI_API_KEY": "",
        "OPENAI_BASE_URL": "",
    }
    defaults.update(overrides)
    for key, value in defaults.items():
        os.environ[key] = value


# ---------------------------------------------------------------------------
# 配置解析
# ---------------------------------------------------------------------------


def test_stt_config_explicit_openai(stt_key) -> None:
    _configure(stt_key=stt_key)
    config = get_stt_config()
    assert config is not None
    assert config.provider == "openai"
    assert config.model == "whisper-1"
    assert config.api_key == stt_key


def test_stt_config_dots_mode_requires_explicit_config(dots_key) -> None:
    _configure(VOICE_STT_PROVIDER="dots", VOICE_STT_BASE_URL="", VOICE_STT_API_KEY="", VOICE_STT_MODEL="")
    # dots 模式不回落 OPENAI_*：配置不全即视为未配置（宁可麦克风隐藏，
    # 不可把 multipart 请求静默打进 chat completions 端点）。
    assert get_stt_config() is None


def test_stt_config_falls_back_to_openai_when_unconfigured(stt_key) -> None:
    import os

    _configure(VOICE_STT_PROVIDER="", VOICE_STT_BASE_URL="", VOICE_STT_API_KEY="")
    # 上面 OPENAI_API_KEY 置空 → 全空 → 未配置
    assert get_stt_config() is None
    os.environ["OPENAI_API_KEY"] = stt_key
    os.environ["OPENAI_BASE_URL"] = "https://api.main.com/v1"
    get_settings.cache_clear()  # get_settings 已缓存第一次构造，需清缓存重读
    config = get_stt_config()
    assert config is not None and config.provider == "openai"
    assert config.api_key == stt_key


def test_stt_unconfigured_when_nothing_set(stt_key) -> None:
    _configure(VOICE_STT_PROVIDER="", VOICE_STT_BASE_URL="", VOICE_STT_API_KEY="")
    assert get_stt_config() is None


def test_tts_config(tts_key) -> None:
    _configure(tts_key=tts_key)
    config = get_tts_config()
    assert config is not None
    assert config.voice == "alloy"


def test_tts_unconfigured() -> None:
    _configure(VOICE_TTS_BASE_URL="", VOICE_TTS_API_KEY="")
    assert get_tts_config() is None


# ---------------------------------------------------------------------------
# SSRF 防护
# ---------------------------------------------------------------------------


def test_ssrf_rejects_loopback_and_private() -> None:
    for bad in (
        "http://localhost:8000/v1/voice/tmp/x",
        "http://127.0.0.1/v1/voice/tmp/x",
        "http://10.0.0.5/x",
        "http://192.168.1.1/x",
        "http://172.16.0.1/x",
        "file:///etc/passwd",
        "ftp://example.com/x",
        "http://169.254.169.254/latest/meta-data",
        "http://[::1]/x",
    ):
        with pytest.raises(VoiceProviderError):
            validate_public_http_url(bad)


def test_ssrf_rejects_host_resolving_to_private(monkeypatch) -> None:
    import socket

    def fake_getaddrinfo(host, *args, **kwargs):
        if host == "rebind.example.com":
            return [(2, 1, 6, "", ("127.0.0.1", 0))]
        raise OSError("no resolve")

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    with pytest.raises(VoiceProviderError):
        validate_public_http_url("https://rebind.example.com/v1/voice/tmp/x")


def test_ssrf_accepts_public_https() -> None:
    # example.com 真实可解析为公网 IP；断言校验通过并原样返回。
    url = validate_public_http_url("https://storage.googleapis.com/example.mp3")
    assert url.startswith("https://")


# ---------------------------------------------------------------------------
# MiMo（小米）：STT input_audio + TTS chat completions（wav / SSE pcm16）
# ---------------------------------------------------------------------------


def test_stt_config_mimo_defaults(stt_key) -> None:
    _configure(stt_key=stt_key, VOICE_STT_PROVIDER="mimo", VOICE_STT_BASE_URL="", VOICE_STT_MODEL="")
    config = get_stt_config()
    assert config is not None and config.provider == "mimo"
    assert config.base_url == "https://api.xiaomimimo.com/v1"
    assert config.model == "mimo-v2.5-asr"


def test_transcribe_mimo_requires_key() -> None:
    _configure(stt_key="", VOICE_STT_PROVIDER="mimo", VOICE_STT_BASE_URL="", VOICE_STT_MODEL="")
    assert get_stt_config() is None


def test_transcribe_mimo_success(monkeypatch, stt_key) -> None:
    _configure(stt_key=stt_key, VOICE_STT_PROVIDER="mimo", VOICE_STT_BASE_URL="", VOICE_STT_MODEL="")
    captured: dict = {}

    def fake_post(url, **kwargs):
        captured["url"] = url
        captured["payload"] = kwargs["json"]
        assert kwargs["headers"]["Authorization"] == f"Bearer {stt_key}"
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": " 我有点焦虑 "}}]},
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr("psych_support_bot.infra.voice.adapter._client.post", fake_post)
    assert transcribe(b"audio", "a.wav") == "我有点焦虑"
    assert captured["url"].endswith("/chat/completions")
    block = captured["payload"]["messages"][0]["content"][0]
    assert block["type"] == "input_audio"
    assert block["input_audio"]["data"].startswith("data:audio/wav;base64,")
    assert captured["payload"]["asr_options"] == {"language": "zh"}


def test_transcribe_mimo_retries_on_429(monkeypatch, stt_key) -> None:
    """免费期上游偶发 429：单次退避重试后成功，不直接打成用户可感的失败。"""
    _configure(stt_key=stt_key, VOICE_STT_PROVIDER="mimo", VOICE_STT_BASE_URL="", VOICE_STT_MODEL="")
    calls = {"n": 0}

    def fake_post(url, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, request=httpx.Request("POST", url))
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "好"}}]},
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr("psych_support_bot.infra.voice.adapter._client.post", fake_post)
    monkeypatch.setattr("psych_support_bot.infra.voice.adapter.time.sleep", lambda s: None)
    assert transcribe(b"audio", "a.wav") == "好"
    assert calls["n"] == 2


def test_transcribe_mimo_429_twice_raises(monkeypatch, stt_key) -> None:
    """重试后仍 429：收敛为 VoiceProviderError（502 → 前端看门狗/降级接管）。"""
    _configure(stt_key=stt_key, VOICE_STT_PROVIDER="mimo", VOICE_STT_BASE_URL="", VOICE_STT_MODEL="")
    calls = {"n": 0}

    def fake_post(url, **kwargs):
        calls["n"] += 1
        return httpx.Response(429, request=httpx.Request("POST", url))

    monkeypatch.setattr("psych_support_bot.infra.voice.adapter._client.post", fake_post)
    monkeypatch.setattr("psych_support_bot.infra.voice.adapter.time.sleep", lambda s: None)
    with pytest.raises(VoiceProviderError):
        transcribe(b"audio", "a.wav")
    assert calls["n"] == 2  # 恰好一次重试，不无限打


def test_tts_config_mimo(tts_key) -> None:
    _configure(
        tts_key=tts_key,
        VOICE_TTS_PROVIDER="mimo",
        VOICE_TTS_BASE_URL="",
        VOICE_TTS_MODEL="",
        VOICE_TTS_VOICE="",
    )
    config = get_tts_config()
    assert config is not None and config.provider == "mimo"
    assert config.base_url == "https://api.xiaomimimo.com/v1"
    assert config.model == "mimo-v2.5-tts"
    assert config.voice == "茉莉"
    assert config.ws_url == ""


def test_synthesize_mimo_success(monkeypatch, tts_key) -> None:
    _configure(
        tts_key=tts_key,
        VOICE_TTS_PROVIDER="mimo",
        VOICE_TTS_BASE_URL="",
        VOICE_TTS_MODEL="",
        VOICE_TTS_VOICE="",
    )

    def fake_post(url, **kwargs):
        assert url.endswith("/chat/completions")
        assert kwargs["json"]["audio"] == {"format": "wav", "voice": "茉莉"}
        assert kwargs["json"]["messages"][0]["role"] == "assistant"
        return httpx.Response(
            200,
            json={"choices": [{"message": {"audio": {"data": base64.b64encode(b"WAVDATA").decode()}}}]},
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr("psych_support_bot.infra.voice.adapter._client.post", fake_post)
    assert synthesize("你好") == b"WAVDATA"


def test_synthesize_stream_mimo_sse(monkeypatch, tts_key) -> None:
    _configure(
        tts_key=tts_key,
        VOICE_TTS_PROVIDER="mimo",
        VOICE_TTS_BASE_URL="",
        VOICE_TTS_MODEL="",
        VOICE_TTS_VOICE="",
    )
    sse_lines = [
        'data: {"choices":[{"delta":{"audio":{"data":"' + base64.b64encode(b"chunk1").decode() + '"}}}]}',
        'data: {"choices":[{"delta":{"audio":{"data":"' + base64.b64encode(b"chunk2").decode() + '"}}}]}',
        "data: [DONE]",
    ]

    class _FakeSSE:
        status_code = 200

        def __init__(self, lines):
            self._lines = lines

        def iter_lines(self):
            return iter(self._lines)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    captured: dict = {}

    def fake_stream(method, url, **kwargs):
        captured["payload"] = kwargs["json"]
        return _FakeSSE(sse_lines)

    monkeypatch.setattr("psych_support_bot.infra.voice.adapter._client.stream", fake_stream)
    assert list(synthesize_stream("慢慢来")) == [b"chunk1", b"chunk2"]  # SSE delta 逐块产出
    assert captured["payload"]["audio"]["format"] == "wav"


def _mimo_tts_env(tts_key: str) -> None:
    _configure(
        tts_key=tts_key,
        VOICE_TTS_PROVIDER="mimo",
        VOICE_TTS_BASE_URL="",
        VOICE_TTS_MODEL="",
        VOICE_TTS_VOICE="",
    )


def _sse_body(chunks: list[bytes], *, done: bool = True) -> list[str]:
    lines = ['data: {"choices":[{"delta":{"audio":{"data":"' + base64.b64encode(c).decode() + '"}}}]}' for c in chunks]
    return lines + (["data: [DONE]"] if done else [])


class _FakeSSEResponse:
    """上下文管理器式的假 SSE 响应；lines 可为 str 或 Exception（迭代到该处抛出）。"""

    status_code = 200

    def __init__(self, lines: list):
        self._lines = list(lines)

    def iter_lines(self):
        for item in self._lines:
            if isinstance(item, Exception):
                raise item
            yield item

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def test_mimo_stream_midstream_failure_does_not_retry(monkeypatch, tts_key) -> None:
    """已产出块后断流：直接抛错，不得整体重试（重试=调用方收到重复前缀，
    live 路径表现为音频重复念开头；重复内容还会写进 TTS 缓存）。"""
    _mimo_tts_env(tts_key)
    calls = {"n": 0}

    def fake_stream(method, url, **kwargs):
        calls["n"] += 1
        return _FakeSSEResponse([*_sse_body([b"c1", b"c2"], done=False), httpx.ReadTimeout("dropped mid-stream")])

    monkeypatch.setattr("psych_support_bot.infra.voice.adapter._client.stream", fake_stream)
    monkeypatch.setattr("psych_support_bot.infra.voice.adapter.time.sleep", lambda s: None)
    gen = synthesize_stream("慢慢来")
    assert next(gen) == b"c1"
    assert next(gen) == b"c2"
    with pytest.raises(VoiceProviderError):
        next(gen)
    assert calls["n"] == 1  # 中途失败不发第二次请求


def test_mimo_stream_pre_first_byte_failure_retries(monkeypatch, tts_key) -> None:
    """首字节前失败（连接/打开阶段）：零产出，允许单次重试（原语义保留）。"""
    _mimo_tts_env(tts_key)
    calls = {"n": 0}

    def fake_stream(method, url, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ConnectError("conn refused")
        return _FakeSSEResponse(_sse_body([b"c1"]))

    monkeypatch.setattr("psych_support_bot.infra.voice.adapter._client.stream", fake_stream)
    monkeypatch.setattr("psych_support_bot.infra.voice.adapter.time.sleep", lambda s: None)
    assert list(synthesize_stream("慢慢来")) == [b"c1"]
    assert calls["n"] == 2


def test_tts_media_type_mimo_vs_others(tts_key) -> None:
    import psych_support_bot.infra.voice.adapter as _adapter

    _configure(
        tts_key=tts_key,
        VOICE_TTS_PROVIDER="mimo",
        VOICE_TTS_BASE_URL="",
        VOICE_TTS_MODEL="",
        VOICE_TTS_VOICE="",
    )
    get_settings.cache_clear()
    assert _adapter.tts_media_type() == "audio/wav"
    _configure(tts_key=tts_key)  # openai → mp3 容器
    get_settings.cache_clear()
    assert _adapter.tts_media_type() == "audio/mpeg"


# ---------------------------------------------------------------------------
# STT 调用（mock httpx）
# ---------------------------------------------------------------------------


def test_transcribe_openai_success(monkeypatch, stt_key) -> None:
    _configure(stt_key=stt_key)

    def fake_post(url, **kwargs):
        assert url.endswith("/audio/transcriptions")
        assert kwargs["headers"]["Authorization"] == f"Bearer {stt_key}"
        assert kwargs["data"]["model"] == "whisper-1"
        return httpx.Response(200, json={"text": " 我现在心跳很快 "}, request=httpx.Request("POST", url))

    monkeypatch.setattr("psych_support_bot.infra.voice.adapter._client.post", fake_post)
    assert transcribe(b"audio", "a.webm") == "我现在心跳很快"


def test_transcribe_unconfigured_raises_not_configured() -> None:
    _configure(VOICE_STT_PROVIDER="", VOICE_STT_BASE_URL="", VOICE_STT_API_KEY="")
    with pytest.raises(VoiceNotConfigured):
        transcribe(b"audio", "a.webm")


def test_transcribe_openai_upstream_error(stt_key, monkeypatch) -> None:
    _configure(stt_key=stt_key)
    calls = {"n": 0}

    def fake_post(url, **kw):
        calls["n"] += 1
        return httpx.Response(500, request=httpx.Request("POST", url))

    monkeypatch.setattr("psych_support_bot.infra.voice.adapter._client.post", fake_post)
    monkeypatch.setattr("psych_support_bot.infra.voice.adapter.time.sleep", lambda s: None)
    with pytest.raises(VoiceProviderError):
        transcribe(b"audio", "a.webm")
    assert calls["n"] == 2  # 5xx 属瞬时失败：单次退避重试后仍失败才抛


def test_transcribe_openai_retries_on_5xx(monkeypatch, stt_key) -> None:
    """重试策略全供应商统一：openai STT 5xx 也走单次退避重试（此前仅 MiMo 有）。"""
    _configure(stt_key=stt_key)
    calls = {"n": 0}

    def fake_post(url, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(502, request=httpx.Request("POST", url))
        return httpx.Response(200, json={"text": "恢复了"}, request=httpx.Request("POST", url))

    monkeypatch.setattr("psych_support_bot.infra.voice.adapter._client.post", fake_post)
    monkeypatch.setattr("psych_support_bot.infra.voice.adapter.time.sleep", lambda s: None)
    assert transcribe(b"audio", "a.webm") == "恢复了"
    assert calls["n"] == 2


def test_transcribe_minimax_success(monkeypatch, tts_key) -> None:
    _configure(
        stt_key=tts_key,
        VOICE_STT_PROVIDER="minimax",
        VOICE_STT_BASE_URL="",
        VOICE_STT_MODEL="",
    )
    config = get_stt_config()
    assert config is not None and config.provider == "minimax"
    # base_url/model 缺省值：国内站 + asr-1.0
    assert config.base_url == "https://api.minimax.cn/v1"
    assert config.model == "asr-1.0"

    def fake_post(url, **kwargs):
        assert url == "https://api.minimax.cn/v1/speech_to_text"
        assert kwargs["headers"]["Authorization"] == f"Bearer {tts_key}"
        assert kwargs["data"]["model"] == "asr-1.0"
        assert kwargs["data"]["language"] == "zh"
        filename, content = kwargs["files"]["file"][0], kwargs["files"]["file"][1]
        assert filename == "a.m4a" and content == b"audio"
        return httpx.Response(
            200,
            json={"text": " 我现在心跳很快 ", "duration": 2.1, "base_resp": {"status_code": 0}},
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr("psych_support_bot.infra.voice.adapter._client.post", fake_post)
    assert transcribe(b"audio", "a.m4a") == "我现在心跳很快"


def test_transcribe_minimax_upstream_error(monkeypatch, tts_key) -> None:
    _configure(
        stt_key=tts_key,
        VOICE_STT_PROVIDER="minimax",
        VOICE_STT_MODEL="",
    )

    def fake_post(url, **kwargs):
        # MiniMax 风格业务错误：HTTP 200 但 base_resp.status_code 非 0
        return httpx.Response(
            200,
            json={"base_resp": {"status_code": 1004, "status_msg": "invalid params"}},
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr("psych_support_bot.infra.voice.adapter._client.post", fake_post)
    with pytest.raises(VoiceProviderError):
        transcribe(b"audio", "a.m4a")


def test_transcribe_minimax_requires_key() -> None:
    _configure(VOICE_STT_PROVIDER="minimax", VOICE_STT_API_KEY="")
    assert get_stt_config() is None


def test_transcribe_dots_mode_full_flow(monkeypatch, dots_key) -> None:

    _configure(
        stt_key=dots_key,
        VOICE_STT_PROVIDER="dots",
        VOICE_STT_BASE_URL="https://dots.example.com/v1",
        VOICE_STT_MODEL="dots3-note-prev",
    )

    captured: dict = {}

    def fake_post(url, **kwargs):
        captured["url"] = url
        if url.endswith("/chat/completions"):
            audio_url = kwargs["json"]["messages"][0]["content"][0]["audio_url"]["url"]
            # audio_url 为 base64 data URI 内联：header + 可逆解码
            scheme, b64 = audio_url.split(";base64,", 1)
            assert scheme == "data:audio/webm"
            assert base64.b64decode(b64) == b"audio-bytes"
            assert kwargs["headers"]["api-key"] == dots_key
            return httpx.Response(
                200,
                json={"choices": [{"message": {"content": "我心跳好快。"}}]},
                request=httpx.Request("POST", url),
            )
        raise AssertionError(url)

    monkeypatch.setattr("psych_support_bot.infra.voice.adapter._client.post", fake_post)
    assert transcribe(b"audio-bytes", "a.webm") == "我心跳好快。"
    assert captured["url"].endswith("/chat/completions")


def test_transcribe_dots_media_type_by_extension(monkeypatch, dots_key) -> None:
    _configure(
        stt_key=dots_key,
        VOICE_STT_PROVIDER="dots",
        VOICE_STT_BASE_URL="https://dots.example.com/v1",
        VOICE_STT_MODEL="m",
    )
    mimes: list[str] = []

    def fake_post(url, **kwargs):
        audio_url = kwargs["json"]["messages"][0]["content"][0]["audio_url"]["url"]
        mimes.append(audio_url.split(";base64,", 1)[0])
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "好"}}]},
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr("psych_support_bot.infra.voice.adapter._client.post", fake_post)
    transcribe(b"a", "clip.m4a")
    transcribe(b"a", "note.mp3")
    transcribe(b"a", "noext")
    assert mimes == ["data:audio/mp4", "data:audio/mpeg", "data:audio/webm"]


def test_transcribe_dots_upstream_error(monkeypatch, dots_key) -> None:

    _configure(
        stt_key=dots_key,
        VOICE_STT_PROVIDER="dots",
        VOICE_STT_BASE_URL="https://dots.example.com/v1",
        VOICE_STT_MODEL="m",
    )

    def fake_post(url, **kwargs):
        return httpx.Response(502, request=httpx.Request("POST", url))

    monkeypatch.setattr("psych_support_bot.infra.voice.adapter._client.post", fake_post)
    monkeypatch.setattr("psych_support_bot.infra.voice.adapter.time.sleep", lambda s: None)
    with pytest.raises(VoiceProviderError):
        transcribe(b"audio-bytes", "a.webm")


# ---------------------------------------------------------------------------
# STT 上下文词表（VOICE_STT_PROMPT：内置默认 / 覆盖 / 禁用 / 各供应商差异）
# ---------------------------------------------------------------------------


def test_stt_prompt_default_vocab_attached_to_openai(monkeypatch, stt_key) -> None:
    _configure(stt_key=stt_key, VOICE_STT_PROMPT="")
    captured: dict = {}

    def fake_post(url, **kwargs):
        captured.update(kwargs["data"])
        return httpx.Response(200, json={"text": "好"}, request=httpx.Request("POST", url))

    monkeypatch.setattr("psych_support_bot.infra.voice.adapter._client.post", fake_post)
    transcribe(b"a", "a.webm")
    assert "正念" in captured.get("prompt", "")  # 缺省即用内置心理陪伴词表


def test_stt_prompt_override_and_disable_openai(monkeypatch, stt_key) -> None:
    import os

    captured: dict = {}

    def fake_post(url, **kwargs):
        captured.clear()
        captured.update(kwargs["data"])
        return httpx.Response(200, json={"text": "好"}, request=httpx.Request("POST", url))

    monkeypatch.setattr("psych_support_bot.infra.voice.adapter._client.post", fake_post)
    _configure(stt_key=stt_key, VOICE_STT_PROMPT="呼吸 肌肉")
    get_settings.cache_clear()
    transcribe(b"a", "a.webm")
    assert captured.get("prompt") == "呼吸 肌肉"  # 显式配置覆盖内置
    os.environ["VOICE_STT_PROMPT"] = "off"
    get_settings.cache_clear()
    transcribe(b"a", "a.webm")
    assert "prompt" not in captured  # off 即禁用，不发该字段


def test_stt_prompt_in_dots_instruction(monkeypatch, dots_key) -> None:
    _configure(
        stt_key=dots_key,
        VOICE_STT_PROVIDER="dots",
        VOICE_STT_BASE_URL="https://dots.example.com/v1",
        VOICE_STT_MODEL="m",
        VOICE_STT_PROMPT="",
    )
    texts: list[str] = []

    def fake_post(url, **kwargs):
        content = kwargs["json"]["messages"][0]["content"]
        texts.append(content[1]["text"])
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "好"}}]},
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr("psych_support_bot.infra.voice.adapter._client.post", fake_post)
    transcribe(b"a", "a.webm")
    assert "讲话者可能用到这些词" in texts[0] and "恐慌" in texts[0]


def test_stt_prompt_not_sent_to_minimax(monkeypatch, tts_key) -> None:
    """minimax /speech_to_text 无 prompt 参数：词表不得混进 form data。"""
    _configure(
        stt_key=tts_key,
        VOICE_STT_PROVIDER="minimax",
        VOICE_STT_MODEL="",
        VOICE_STT_PROMPT="",
    )
    captured: dict = {}

    def fake_post(url, **kwargs):
        captured.update(kwargs["data"])
        return httpx.Response(
            200,
            json={"text": "好", "base_resp": {"status_code": 0}},
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr("psych_support_bot.infra.voice.adapter._client.post", fake_post)
    transcribe(b"a", "a.m4a")
    assert "prompt" not in captured


def test_stt_prompt_language_selection(stt_key) -> None:
    from psych_support_bot.infra.voice.adapter import SttConfig, _stt_prompt_for

    cfg = SttConfig(provider="openai", base_url="", api_key="", model="", language="", prompt="")
    assert "着陆练习" in _stt_prompt_for(cfg, "")  # 未标注语种 → 中文词表（产品主语种）
    assert "mindfulness" in _stt_prompt_for(cfg, "en").lower()  # en → 英文词表


# ---------------------------------------------------------------------------
# TTS 调用
# ---------------------------------------------------------------------------


def test_synthesize_success(monkeypatch, tts_key) -> None:
    _configure(tts_key=tts_key)

    def fake_post(url, **kwargs):
        assert url.endswith("/audio/speech")
        assert kwargs["json"]["input"].strip()
        assert kwargs["json"]["response_format"] == "mp3"
        return httpx.Response(200, content=b"ID3mp3bytes", request=httpx.Request("POST", url))

    monkeypatch.setattr("psych_support_bot.infra.voice.adapter._client.post", fake_post)
    assert synthesize("你好") == b"ID3mp3bytes"


def test_synthesize_unconfigured() -> None:
    _configure(VOICE_TTS_BASE_URL="", VOICE_TTS_API_KEY="")
    with pytest.raises(VoiceNotConfigured):
        synthesize("你好")


def test_synthesize_rejects_empty_text(tts_key) -> None:
    _configure(tts_key=tts_key)
    with pytest.raises(VoiceProviderError):
        synthesize("   ")


# ---------------------------------------------------------------------------
# TTS 文本缓存（练习须知等重复文案零成本）
# ---------------------------------------------------------------------------


def test_tts_cache_hits_same_text(monkeypatch, tts_key) -> None:
    _configure(tts_key=tts_key)
    calls = {"n": 0}

    def fake_post(url, **kwargs):
        calls["n"] += 1
        return httpx.Response(200, content=b"ID3mp3", request=httpx.Request("POST", url))

    monkeypatch.setattr("psych_support_bot.infra.voice.adapter._client.post", fake_post)
    first = synthesize("你好")
    second = synthesize("你好")
    assert first == second == b"ID3mp3"
    assert calls["n"] == 1  # 第二次命中缓存，不打上游
    # 文本不同 → 不同 key，照打上游
    synthesize("再见")
    assert calls["n"] == 2


def test_tts_cache_key_includes_voice_config(monkeypatch, tts_key) -> None:
    _configure(tts_key=tts_key)
    calls = {"n": 0}

    def fake_post(url, **kwargs):
        calls["n"] += 1
        return httpx.Response(200, content=b"x", request=httpx.Request("POST", url))

    monkeypatch.setattr("psych_support_bot.infra.voice.adapter._client.post", fake_post)
    synthesize("你好")
    # 换音色 → key 不同 → 不吃旧缓存
    _configure(tts_key=tts_key, VOICE_TTS_VOICE="nova")
    get_settings.cache_clear()
    synthesize("你好")
    assert calls["n"] == 2


def test_tts_cache_does_not_cache_errors(monkeypatch, tts_key) -> None:
    _configure(tts_key=tts_key)
    calls = {"n": 0}

    def fake_post(url, **kwargs):
        calls["n"] += 1
        return httpx.Response(500, request=httpx.Request("POST", url))

    monkeypatch.setattr("psych_support_bot.infra.voice.adapter._client.post", fake_post)
    monkeypatch.setattr("psych_support_bot.infra.voice.adapter.time.sleep", lambda s: None)
    with pytest.raises(VoiceProviderError):
        synthesize("你好")
    with pytest.raises(VoiceProviderError):
        synthesize("你好")
    assert calls["n"] == 4  # 失败不缓存，每次照打（5xx 含单次重试：2×2）


def test_tts_cache_lru_eviction(monkeypatch, tts_key) -> None:
    import psych_support_bot.infra.voice.adapter as _adapter

    _configure(tts_key=tts_key)
    monkeypatch.setattr(_adapter, "_TTS_CACHE_MAX_ENTRIES", 2)

    def fake_post(url, **kwargs):
        return httpx.Response(200, content=b"x", request=httpx.Request("POST", url))

    monkeypatch.setattr("psych_support_bot.infra.voice.adapter._client.post", fake_post)
    synthesize("一")
    synthesize("二")
    synthesize("三")  # 容量 2："一" 被逐出
    assert len(_adapter._tts_cache) == 2
    synthesize("一")  # 缓存未命中重建
    assert len(_adapter._tts_cache) == 2


def test_tts_cache_ttl_expiry(monkeypatch, tts_key) -> None:
    import psych_support_bot.infra.voice.adapter as _adapter

    _configure(tts_key=tts_key)
    calls = {"n": 0}

    def fake_post(url, **kwargs):
        calls["n"] += 1
        return httpx.Response(200, content=b"x", request=httpx.Request("POST", url))

    monkeypatch.setattr("psych_support_bot.infra.voice.adapter._client.post", fake_post)
    synthesize("你好")
    real_monotonic = time.monotonic
    monkeypatch.setattr(
        _adapter.time,
        "monotonic",
        lambda: real_monotonic() + _adapter._TTS_CACHE_TTL_SECONDS + 1,
    )
    synthesize("你好")  # TTL 过期 → 重打
    assert calls["n"] == 2


# ---------------------------------------------------------------------------
# 流式合成（边合成边播）
# ---------------------------------------------------------------------------


def test_synthesize_stream_minimax_yields_incremental(monkeypatch, tts_key) -> None:
    _configure_minimax(tts_key)
    hex_a = b"chunk-a".hex()
    hex_b = b"chunk-b".hex()
    fake = _FakeWebSocket(
        [
            {"event": "connected_success", "base_resp": {"status_code": 0}},
            {"event": "task_started", "base_resp": {"status_code": 0}},
            {"data": {"audio": hex_a}, "is_final": True, "base_resp": {"status_code": 0}},
            {"event": "sentence_end", "base_resp": {"status_code": 0}},
            {"data": {"audio": hex_b}, "is_final": False, "base_resp": {"status_code": 0}},
            {"event": "task_finished", "base_resp": {"status_code": 0}},
        ]
    )

    def fake_connect(url, **kwargs):
        return fake

    import psych_support_bot.infra.voice.adapter as _adapter

    monkeypatch.setattr(_adapter.websockets, "connect", fake_connect)
    chunks = list(synthesize_stream("慢慢来"))
    assert chunks == [b"chunk-a", b"chunk-b"]  # 逐块产出（边合成边播）
    assert fake.sent[-1]["event"] == "task_finish"


def test_synthesize_stream_cached_single_chunk(monkeypatch, tts_key) -> None:
    _configure_minimax(tts_key)
    calls = {"n": 0}

    def fake_post(url, **kwargs):
        calls["n"] += 1
        return httpx.Response(200, content=b"x", request=httpx.Request("POST", url))

    monkeypatch.setattr("psych_support_bot.infra.voice.adapter._client.post", fake_post)
    # openai 路径单块产出
    _configure(tts_key=tts_key)
    monkeypatch.setattr("psych_support_bot.infra.voice.adapter._client.post", fake_post)
    assert list(synthesize_stream("你好")) == [b"x"]
    assert list(synthesize_stream("你好")) == [b"x"]  # 命中缓存：单块整段
    assert calls["n"] == 1


# ---------------------------------------------------------------------------
# MiniMax TTS（wss /ws/v1/t2a_v2_bidi，mock websockets）
# ---------------------------------------------------------------------------


class _FakeWebSocket:
    """按脚本回放的假 WS：recv 依次弹出（dict→json 帧，Exception→抛出）。"""

    def __init__(self, script: list) -> None:
        self._script = list(script)
        self.sent: list[dict] = []

    async def send(self, raw: str) -> None:
        import json as _json

        self.sent.append(_json.loads(raw))

    async def recv(self) -> str:
        import json as _json

        if not self._script:
            raise TimeoutError("script exhausted")
        item = self._script.pop(0)
        if isinstance(item, Exception):
            raise item
        return _json.dumps(item)

    async def __aenter__(self) -> "_FakeWebSocket":
        return self

    async def __aexit__(self, *args) -> None:
        return None


def _configure_minimax(tts_key: str, ws_url: str = "wss://api.minimax.cn/ws/v1/t2a_v2_bidi", **overrides) -> None:

    _configure(
        tts_key=tts_key,
        VOICE_TTS_PROVIDER="minimax",
        VOICE_TTS_WS_URL=ws_url,
        VOICE_TTS_MODEL="speech-2.8-hd",
        VOICE_TTS_VOICE="male-qn-qingse",
        **overrides,
    )


def test_tts_config_minimax(tts_key) -> None:
    _configure_minimax(tts_key)
    config = get_tts_config()
    assert config is not None and config.provider == "minimax"
    assert config.model == "speech-2.8-hd"
    assert config.voice == "male-qn-qingse"


def test_tts_config_minimax_requires_key() -> None:
    _configure(VOICE_TTS_PROVIDER="minimax", VOICE_TTS_API_KEY="", VOICE_TTS_BASE_URL="")
    assert get_tts_config() is None


def test_synthesize_minimax_success(monkeypatch, tts_key) -> None:
    _configure_minimax(tts_key)
    hex_a = b"chunk-a".hex()
    hex_b = b"chunk-b".hex()
    fake = _FakeWebSocket(
        [
            {"event": "connected_success", "base_resp": {"status_code": 0}},
            {"event": "task_started", "base_resp": {"status_code": 0}},
            {"data": {"audio": hex_a}, "is_final": True, "base_resp": {"status_code": 0}},
            {"data": {"audio": hex_b}, "is_final": False, "base_resp": {"status_code": 0}},
            {"event": "task_finished", "base_resp": {"status_code": 0}},
        ]
    )

    def fake_connect(url, **kwargs):
        assert url == "wss://api.minimax.cn/ws/v1/t2a_v2_bidi"
        assert kwargs["additional_headers"]["Authorization"] == f"Bearer {tts_key}"
        return fake

    import psych_support_bot.infra.voice.adapter as _adapter

    monkeypatch.setattr(_adapter.websockets, "connect", fake_connect)
    assert synthesize("慢慢来") == b"chunk-a" + b"chunk-b"
    events = [frame.get("event") for frame in fake.sent]
    assert events[0] == "task_start"
    assert events[1] == "task_continue"
    assert fake.sent[1]["text"] == "慢慢来"
    assert events[-1] == "task_finish"


def test_synthesize_minimax_long_text_not_truncated_at_is_final(monkeypatch, tts_key) -> None:
    """多句长文本：每句各有一次 is_final=true，只有 task_finished 是会话终点。

    回归用例（生产 bug：循环把 is_final 当总终点，长回复只朗读第一句）。
    """
    _configure_minimax(tts_key)
    first_sentence = b"sentence-one-audio".hex()
    second_sentence = b"sentence-two-audio".hex()
    third_sentence = b"sentence-three-audio".hex()
    fake = _FakeWebSocket(
        [
            {"event": "connected_success", "base_resp": {"status_code": 0}},
            {"event": "task_started", "base_resp": {"status_code": 0}},
            # 第一句：sentence_start → 分块 → sentence_end（末块 is_final=true）
            {"event": "sentence_start", "base_resp": {"status_code": 0}},
            {"data": {"audio": first_sentence}, "is_final": True, "base_resp": {"status_code": 0}},
            {"event": "sentence_end", "base_resp": {"status_code": 0}},
            # 第二句
            {"event": "sentence_start", "base_resp": {"status_code": 0}},
            {"data": {"audio": second_sentence}, "is_final": True, "base_resp": {"status_code": 0}},
            {"event": "sentence_end", "base_resp": {"status_code": 0}},
            # 第三句 + 会话终点
            {"event": "sentence_start", "base_resp": {"status_code": 0}},
            {"data": {"audio": third_sentence}, "is_final": True, "base_resp": {"status_code": 0}},
            {"event": "sentence_end", "base_resp": {"status_code": 0}},
            {"event": "task_finished", "base_resp": {"status_code": 0}},
        ]
    )

    def fake_connect(url, **kwargs):
        return fake

    import psych_support_bot.infra.voice.adapter as _adapter

    monkeypatch.setattr(_adapter.websockets, "connect", fake_connect)
    audio = synthesize("第一句。第二句。第三句。")
    assert audio == b"sentence-one-audio" + b"sentence-two-audio" + b"sentence-three-audio"
    # task_finish 仍在终点事件之后发送
    assert fake.sent[-1]["event"] == "task_finish"


def test_synthesize_minimax_task_failed(monkeypatch, tts_key) -> None:
    _configure_minimax(tts_key)
    fake = _FakeWebSocket(
        [
            {"event": "connected_success", "base_resp": {"status_code": 0}},
            {"event": "task_started", "base_resp": {"status_code": 0}},
            {"event": "task_failed", "base_resp": {"status_code": 1004, "status_msg": "bad voice"}},
        ]
    )

    def fake_connect(url, **kwargs):
        return fake

    import psych_support_bot.infra.voice.adapter as _adapter

    monkeypatch.setattr(_adapter.websockets, "connect", fake_connect)
    with pytest.raises(VoiceProviderError):
        synthesize("你好")


def test_synthesize_minimax_handshake_failure(monkeypatch, tts_key) -> None:
    _configure_minimax(tts_key)
    fake = _FakeWebSocket([{"event": "connected_failed", "base_resp": {"status_code": 401}}])

    def fake_connect(url, **kwargs):
        return fake

    import psych_support_bot.infra.voice.adapter as _adapter

    monkeypatch.setattr(_adapter.websockets, "connect", fake_connect)
    with pytest.raises(VoiceProviderError):
        synthesize("你好")


def test_synthesize_minimax_ws_url_ssrf_guard(tts_key) -> None:
    """WS URL 指向内网/环回时拒绝外连（SSRF 约束对 wss 同样生效）。"""
    import websockets

    for bad in (
        "wss://127.0.0.1/ws/v1/t2a_v2_bidi",
        "wss://10.0.0.9/ws/v1/t2a_v2_bidi",
        "ws://localhost/ws/v1/t2a_v2_bidi",
    ):
        _configure_minimax(tts_key, ws_url=bad)
        with pytest.raises(VoiceProviderError):
            synthesize("你好")  # validate 在建连前抛出，不会触达 fake connect
    del websockets


# ---------------------------------------------------------------------------
# 音色复刻（MiMo VoiceClone）：voice=样本文件路径 → 内联 data URI
# ---------------------------------------------------------------------------


def _mimo_config(voice: str):
    from psych_support_bot.infra.voice.adapter import TtsConfig

    return TtsConfig(
        provider="mimo",
        base_url="https://api.xiaomimimo.com/v1",
        ws_url="",
        api_key=_fake_key("mimo"),
        model="mimo-v2.5-tts-voiceclone",
        voice=voice,
    )


def test_voice_reference_preset_name_passthrough() -> None:
    """预置音色名（非文件）原样直传——冰糖等旧行为不变。"""
    from psych_support_bot.infra.voice.adapter import _mimo_voice_reference

    assert _mimo_voice_reference("冰糖") == "冰糖"
    assert _mimo_voice_reference("Chinese (Mandarin)_Warm_Bestie") == "Chinese (Mandarin)_Warm_Bestie"
    assert _mimo_voice_reference("") == ""


def test_voice_reference_file_becomes_data_uri(tmp_path) -> None:
    """存在的 .mp3/.wav 文件按 mime 内联为 data URI（复刻样本语义）。"""
    from psych_support_bot.infra.voice.adapter import _mimo_tts_payload, _mimo_voice_reference

    sample = tmp_path / "ref.mp3"
    sample.write_bytes(b"\xff\xfb" + b"ID3fake-audio" * 4)
    uri = _mimo_voice_reference(str(sample))
    assert uri.startswith("data:audio/mpeg;base64,")
    import base64 as _b64

    assert _b64.b64decode(uri.split(",", 1)[1]) == sample.read_bytes()

    # payload 装配走同一入口（合成/流式共用）
    payload = _mimo_tts_payload(_mimo_config(str(sample)), "你好", stream=True, audio_format="pcm16")
    assert payload["audio"]["voice"] == uri
    assert payload["model"] == "mimo-v2.5-tts-voiceclone"


def test_voice_reference_wav_mime_and_cache(tmp_path) -> None:
    """wav 后缀映射 audio/wav；同文件同 mtime 二次读取命中缓存（不重编码）。"""
    from psych_support_bot.infra.voice import adapter as _adapter
    from psych_support_bot.infra.voice.adapter import _mimo_voice_reference

    sample = tmp_path / "ref.wav"
    sample.write_bytes(b"RIFF....WAVEfmt ")
    first = _mimo_voice_reference(str(sample))
    assert first.startswith("data:audio/wav;base64,")
    assert str(sample) in _adapter._VOICE_REF_CACHE  # 已缓存
    assert _mimo_voice_reference(str(sample)) == first


def test_voice_reference_rejects_unsupported_ext(tmp_path) -> None:
    from psych_support_bot.infra.voice.adapter import _mimo_voice_reference

    sample = tmp_path / "ref.m4a"
    sample.write_bytes(b"\x00\x00\x00")
    with pytest.raises(VoiceProviderError, match=r"\.mp3 or \.wav"):
        _mimo_voice_reference(str(sample))


def test_voice_reference_rejects_oversize(tmp_path, monkeypatch) -> None:
    """base64 超 10MB 拒绝（网关硬限制，配置期报错优于运行期 4xx）。"""
    from psych_support_bot.infra.voice import adapter as _adapter
    from psych_support_bot.infra.voice.adapter import _mimo_voice_reference

    sample = tmp_path / "big.mp3"
    sample.write_bytes(b"\xff\xfb" + b"\x00" * 100)
    monkeypatch.setattr(_adapter, "_VOICE_REF_MAX_B64", 8)  # 压低阈值触发
    with pytest.raises(VoiceProviderError, match="too large"):
        _mimo_voice_reference(str(sample))
