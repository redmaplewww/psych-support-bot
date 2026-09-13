"""语音路由契约测试（TestClient，mock 适配器，无真实网络）。

覆盖：/status 探测、/transcribe 格式与大小校验、/speak 长度校验与
未配置 503。AUTH_ENABLED=false（游客直进）与既有集成测试同口径。
"""

import pytest
from fastapi.testclient import TestClient

from psych_support_bot.app import create_app


@pytest.fixture(autouse=True)
def _isolated_voice_env(monkeypatch):
    """隔离仓库 .env 与上一个用例残留：语音配置默认全空（未配置态）。"""
    # 空字符串覆盖而非 delenv：env var 优先级高于 .env 文件值——
    # 开发机的 .env 可能带真实语音 key（pydantic-settings 会读 env_file），
    # delenv 挡不住它，空串才能强制「未配置」基线。
    for var in (
        "VOICE_STT_PROVIDER",
        "VOICE_STT_BASE_URL",
        "VOICE_STT_API_KEY",
        "VOICE_STT_MODEL",
        "VOICE_TTS_PROVIDER",
        "VOICE_TTS_BASE_URL",
        "VOICE_TTS_API_KEY",
        "VOICE_TTS_MODEL",
        "VOICE_TTS_VOICE",
        "VOICE_TTS_WS_URL",
        "VOICE_TTS_LANGUAGE_BOOST",
        "OPENAI_API_KEY",
        "OPENAI_BASE_URL",
    ):
        monkeypatch.setenv(var, "")
    from psych_support_bot.api.routes import voice as _voice_routes
    from psych_support_bot.infra.config.settings import get_settings
    from psych_support_bot.infra.voice import adapter as _voice_adapter

    _voice_adapter._reset_tts_cache_for_tests()
    _voice_routes._reset_stt_fail_state_for_tests()
    get_settings.cache_clear()
    yield
    _voice_adapter._reset_tts_cache_for_tests()
    _voice_routes._reset_stt_fail_state_for_tests()
    get_settings.cache_clear()


@pytest.fixture()
def client():
    app = create_app()
    with TestClient(app) as test_client:
        yield test_client


def test_voice_status_contract(client, monkeypatch):
    monkeypatch.setenv("VOICE_STT_PROVIDER", "openai")
    monkeypatch.setenv("VOICE_STT_BASE_URL", "https://stt.example.com/v1")
    monkeypatch.setenv("VOICE_STT_API_KEY", "k" * 8)
    monkeypatch.delenv("VOICE_TTS_BASE_URL", raising=False)
    monkeypatch.delenv("VOICE_TTS_API_KEY", raising=False)
    from psych_support_bot.infra.config.settings import get_settings

    get_settings.cache_clear()
    data = client.get("/v1/voice/status").json()
    assert data == {"stt": True, "tts": False, "stt_degraded": False}


def test_voice_status_all_unconfigured(client, monkeypatch):
    # 空字符串覆盖（env var 优先于 .env 文件值）：get_stt_config 对空串视为未配置
    monkeypatch.setenv("VOICE_STT_PROVIDER", "")
    monkeypatch.setenv("OPENAI_API_KEY", "")
    from psych_support_bot.infra.config.settings import get_settings

    get_settings.cache_clear()
    data = client.get("/v1/voice/status").json()
    assert data == {"stt": False, "tts": False, "stt_degraded": False}


def test_transcribe_rejects_unsupported_type(client):
    res = client.post(
        "/v1/voice/transcribe",
        files={"file": ("x.txt", b"not audio", "text/plain")},
    )
    assert res.status_code == 422


def test_transcribe_rejects_empty_and_oversize(client):
    res = client.post(
        "/v1/voice/transcribe",
        files={"file": ("a.webm", b"", "audio/webm")},
    )
    assert res.status_code == 422
    res = client.post(
        "/v1/voice/transcribe",
        files={"file": ("a.webm", b"x" * (26 * 1024 * 1024), "audio/webm")},
    )
    assert res.status_code == 413


def test_transcribe_unconfigured_returns_503(client, monkeypatch):
    monkeypatch.setenv("VOICE_STT_PROVIDER", "")
    monkeypatch.setenv("OPENAI_API_KEY", "")
    from psych_support_bot.infra.config.settings import get_settings

    get_settings.cache_clear()
    res = client.post(
        "/v1/voice/transcribe",
        files={"file": ("a.webm", b"audio", "audio/webm")},
    )
    assert res.status_code == 503


def test_transcribe_success_contract(client, monkeypatch):
    monkeypatch.setenv("VOICE_STT_PROVIDER", "openai")
    monkeypatch.setenv("VOICE_STT_BASE_URL", "https://stt.example.com/v1")
    monkeypatch.setenv("VOICE_STT_API_KEY", "k" * 8)
    from psych_support_bot.infra.config.settings import get_settings

    get_settings.cache_clear()

    import httpx

    def fake_post(url, **kwargs):
        return httpx.Response(200, json={"text": "带我做接地练习"}, request=httpx.Request("POST", url))

    monkeypatch.setattr("psych_support_bot.infra.voice.adapter._client.post", fake_post)
    res = client.post(
        "/v1/voice/transcribe",
        files={"file": ("a.webm", b"audio", "audio/webm")},
    )
    assert res.status_code == 200
    assert res.json() == {"text": "带我做接地练习"}


def test_speak_contract_and_validation(client, monkeypatch):
    monkeypatch.delenv("VOICE_TTS_BASE_URL", raising=False)
    monkeypatch.delenv("VOICE_TTS_API_KEY", raising=False)
    from psych_support_bot.infra.config.settings import get_settings

    get_settings.cache_clear()

    # 空文本 422
    assert client.post("/v1/voice/speak", json={"text": "  "}).status_code == 422
    # 超长 422
    assert client.post("/v1/voice/speak", json={"text": "长" * 1500}).status_code == 422
    # 未配置 503
    assert client.post("/v1/voice/speak", json={"text": "你好"}).status_code == 503


def test_speak_success_returns_audio(client, monkeypatch):
    # 显式 openai 路径（adapter 测试可能残留 VOICE_TTS_PROVIDER=minimax）
    monkeypatch.setenv("VOICE_TTS_PROVIDER", "openai")
    monkeypatch.setenv("VOICE_TTS_BASE_URL", "https://tts.example.com/v1")
    monkeypatch.setenv("VOICE_TTS_API_KEY", "k" * 8)
    from psych_support_bot.infra.config.settings import get_settings

    get_settings.cache_clear()

    import httpx

    monkeypatch.setattr(
        "psych_support_bot.infra.voice.adapter._client.post",
        lambda url, **kw: httpx.Response(200, content=b"ID3", request=httpx.Request("POST", url)),
    )
    res = client.post("/v1/voice/speak", json={"text": "你好"})
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("audio/mpeg")


def test_speak_stream_contract_and_validation(client, monkeypatch):
    monkeypatch.delenv("VOICE_TTS_BASE_URL", raising=False)
    monkeypatch.delenv("VOICE_TTS_API_KEY", raising=False)
    from psych_support_bot.infra.config.settings import get_settings

    get_settings.cache_clear()

    # 空文本 422
    assert client.post("/v1/voice/speak/stream", json={"text": "  "}).status_code == 422
    # 超长 422
    assert client.post("/v1/voice/speak/stream", json={"text": "长" * 1500}).status_code == 422
    # 未配置 503
    assert client.post("/v1/voice/speak/stream", json={"text": "你好"}).status_code == 503


def test_speak_stream_success_returns_audio_stream(client, monkeypatch):
    # 显式 openai 路径（隔离环境已把 provider 置空）
    monkeypatch.setenv("VOICE_TTS_PROVIDER", "openai")
    monkeypatch.setenv("VOICE_TTS_BASE_URL", "https://tts.example.com/v1")
    monkeypatch.setenv("VOICE_TTS_API_KEY", "k" * 8)
    from psych_support_bot.infra.config.settings import get_settings

    get_settings.cache_clear()

    import httpx

    def fake_post(url, **kw):
        return httpx.Response(200, content=b"ID3-stream", request=httpx.Request("POST", url))

    monkeypatch.setattr("psych_support_bot.infra.voice.adapter._client.post", fake_post)
    res = client.post("/v1/voice/speak/stream", json={"text": "你好"})
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("audio/mpeg")
    assert res.content == b"ID3-stream"


def test_tmp_media_endpoint_removed(client):
    # dots STT 已改为 base64 data URI 内联，无鉴权的 /tmp 拉取端点不复存在
    assert client.get("/v1/voice/tmp/deadbeef").status_code == 404


# ---------------------------------------------------------------------------
# STT 故障看门狗（连续失败 → status 降级标志；成功恢复）
# ---------------------------------------------------------------------------


def _configure_openai_stt(monkeypatch) -> None:
    monkeypatch.setenv("VOICE_STT_PROVIDER", "openai")
    monkeypatch.setenv("VOICE_STT_BASE_URL", "https://stt.example.com/v1")
    monkeypatch.setenv("VOICE_STT_API_KEY", "k" * 8)
    from psych_support_bot.infra.config.settings import get_settings

    get_settings.cache_clear()


def test_stt_fail_streak_sets_degraded_then_recovers(client, monkeypatch):
    import httpx

    _configure_openai_stt(monkeypatch)
    fail = lambda url, **kw: httpx.Response(500, request=httpx.Request("POST", url))
    monkeypatch.setattr("psych_support_bot.infra.voice.adapter._client.post", fail)
    monkeypatch.setattr("psych_support_bot.infra.voice.adapter.time.sleep", lambda s: None)  # 跳过 5xx 退避
    for _ in range(3):
        assert (
            client.post("/v1/voice/transcribe", files={"file": ("a.webm", b"audio", "audio/webm")}).status_code == 502
        )
    assert client.get("/v1/voice/status").json()["stt_degraded"] is True
    # 上游恢复：一次成功即归零（degraded 撤销，麦克风路径不留观察态）
    monkeypatch.setattr(
        "psych_support_bot.infra.voice.adapter._client.post",
        lambda url, **kw: httpx.Response(200, json={"text": "好"}, request=httpx.Request("POST", url)),
    )
    assert client.post("/v1/voice/transcribe", files={"file": ("a.webm", b"audio", "audio/webm")}).status_code == 200
    assert client.get("/v1/voice/status").json()["stt_degraded"] is False


def test_stt_fail_below_threshold_not_degraded(client, monkeypatch):
    """未达阈值 3 的连续失败不置 degraded（偶发网络抖动不该掐拾音）。"""
    import httpx

    _configure_openai_stt(monkeypatch)
    monkeypatch.setattr(
        "psych_support_bot.infra.voice.adapter._client.post",
        lambda url, **kw: httpx.Response(500, request=httpx.Request("POST", url)),
    )
    monkeypatch.setattr("psych_support_bot.infra.voice.adapter.time.sleep", lambda s: None)  # 跳过 5xx 退避
    for _ in range(2):  # 未达阈值 3
        assert (
            client.post("/v1/voice/transcribe", files={"file": ("a.webm", b"audio", "audio/webm")}).status_code == 502
        )
    assert client.get("/v1/voice/status").json()["stt_degraded"] is False


def test_stt_unconfigured_503_not_counted_as_failure(client, monkeypatch):
    """配置性 503（未配置）≠ 上游故障：打满次数也不进 streak。"""
    monkeypatch.setenv("VOICE_STT_PROVIDER", "")
    monkeypatch.setenv("OPENAI_API_KEY", "")
    from psych_support_bot.infra.config.settings import get_settings

    get_settings.cache_clear()
    for _ in range(4):
        assert (
            client.post("/v1/voice/transcribe", files={"file": ("a.webm", b"audio", "audio/webm")}).status_code == 503
        )
    _configure_openai_stt(monkeypatch)
    assert client.get("/v1/voice/status").json()["stt_degraded"] is False


# ---------------------------------------------------------------------------
# 回合分阶段耗时打点（/turn_metrics：白名单数值 + 隐私零增量）
# ---------------------------------------------------------------------------


def test_turn_metrics_accepts_whitelisted_numbers(client):
    res = client.post(
        "/v1/voice/turn_metrics",
        json={
            "speech_end_to_stt_ms": 1420,
            "stt_to_first_sentence_ms": 610.4,
            "final_to_first_audio_ms": 900,
            "tts_enabled": True,
        },
    )
    assert res.status_code == 200
    assert res.json() == {"ok": True}


def test_turn_metrics_rejects_garbage(client):
    # 全部非法（未知键/非数/负值/超界）→ 无可记录 → 422
    assert client.post("/v1/voice/turn_metrics", json={"foo": 1}).status_code == 422
    assert client.post("/v1/voice/turn_metrics", json={"speech_end_to_stt_ms": -5}).status_code == 422
    assert client.post("/v1/voice/turn_metrics", json={"speech_end_to_stt_ms": "x"}).status_code == 422
    assert client.post("/v1/voice/turn_metrics", json={}).status_code == 422


def test_turn_metrics_bool_only_is_valid(client):
    # 打字轮没配 TTS：只有开关布尔也值得记录（不算空上报）
    assert client.post("/v1/voice/turn_metrics", json={"tts_enabled": False}).status_code == 200


# ---------------------------------------------------------------------------
# tts_live WS 协议（②PCM 流式起播）：ready 协商 + 二进制音频帧 + 事件序列
# ---------------------------------------------------------------------------


class _FakeMM:
    """按脚本回放的假 MiniMax WS（dict 帧序列；send 记录发出帧）。"""

    def __init__(self, script):
        import json as _json

        self._json = _json
        self._script = list(script)
        self.sent = []

    async def send(self, raw):
        self.sent.append(self._json.loads(raw))

    async def recv(self):
        if not self._script:
            raise TimeoutError("script exhausted")
        item = self._script.pop(0)
        if isinstance(item, Exception):
            raise item
        return self._json.dumps(item)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None


def test_tts_live_pcm_protocol(client, monkeypatch):
    """契约：ready 声明 pcm + 采样率；音频以裸 PCM 二进制帧下发；
    is_final → sentence_end；task_finished → round_end；task_start 用 pcm 无码率。"""
    import websockets

    monkeypatch.setenv("VOICE_TTS_PROVIDER", "minimax")
    monkeypatch.setenv("VOICE_TTS_API_KEY", "k" * 8)
    monkeypatch.setenv("VOICE_TTS_WS_URL", "wss://api.minimax.cn/ws/v1/t2a_v2_bidi")
    from psych_support_bot.infra.config.settings import get_settings

    get_settings.cache_clear()

    pcm = bytes([0x01, 0x02, 0x03, 0x04])
    fake = _FakeMM(
        [
            {"event": "connected_success", "base_resp": {"status_code": 0}},
            {"event": "task_started", "base_resp": {"status_code": 0}},
            {"data": {"audio": pcm.hex()}, "is_final": True, "base_resp": {"status_code": 0}},
            {"event": "task_finished", "base_resp": {"status_code": 0}},
        ]
    )
    monkeypatch.setattr(websockets, "connect", lambda url, **kw: fake)

    with client.websocket_connect("/v1/voice/tts/live") as ws:
        ready = ws.receive_json()
        assert ready["type"] == "ready"
        assert ready["audio"]["format"] == "pcm"
        assert ready["audio"]["sample_rate"] == 32000
        ws.send_json({"type": "say", "text": "你好呀"})
        ws.send_json({"type": "end"})
        assert ws.receive_bytes() == pcm  # 二进制帧 = 裸 PCM，非 base64 JSON
        assert ws.receive_json()["type"] == "sentence_end"
        assert ws.receive_json()["type"] == "round_end"

    start = next(f for f in fake.sent if f.get("event") == "task_start")
    assert start["audio_setting"]["format"] == "pcm"
    assert "bitrate" not in start["audio_setting"]


def test_tts_live_upstream_error_is_surfaced(client, monkeypatch):
    """上游中途报错：error 帧先于 round_end 下发（前端据此转投 HTTP 队列，
    不再表现为"朗读静默消失"）。"""
    import websockets

    monkeypatch.setenv("VOICE_TTS_PROVIDER", "minimax")
    monkeypatch.setenv("VOICE_TTS_API_KEY", "k" * 8)
    monkeypatch.setenv("VOICE_TTS_WS_URL", "wss://api.minimax.cn/ws/v1/t2a_v2_bidi")
    from psych_support_bot.infra.config.settings import get_settings

    get_settings.cache_clear()

    fake = _FakeMM(
        [
            {"event": "connected_success", "base_resp": {"status_code": 0}},
            {"event": "task_started", "base_resp": {"status_code": 0}},
            {"base_resp": {"status_code": 1004, "status_msg": "quota exceeded"}},
        ]
    )
    monkeypatch.setattr(websockets, "connect", lambda url, **kw: fake)

    with client.websocket_connect("/v1/voice/tts/live") as ws:
        assert ws.receive_json()["type"] == "ready"
        ws.send_json({"type": "say", "text": "你好呀"})
        ws.send_json({"type": "end"})
        err = ws.receive_json()
        assert err["type"] == "error" and "1004" in err["detail"]
        assert ws.receive_json()["type"] == "round_end"


# ---------------------------------------------------------------------------
# MiMo provider：/speak 媒体类型 + tts_live SSE→PCM 协议
# ---------------------------------------------------------------------------


def test_speak_mimo_returns_wav(client, monkeypatch):
    import httpx

    monkeypatch.setenv("VOICE_TTS_PROVIDER", "mimo")
    monkeypatch.setenv("VOICE_TTS_API_KEY", "k" * 8)
    from psych_support_bot.infra.config.settings import get_settings

    get_settings.cache_clear()
    monkeypatch.setattr(
        "psych_support_bot.infra.voice.adapter._client.post",
        lambda url, **kw: httpx.Response(
            200,
            json={"choices": [{"message": {"audio": {"data": "V0FW"}}}]},
            request=httpx.Request("POST", url),
        ),
    )
    res = client.post("/v1/voice/speak", json={"text": "你好"})
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("audio/wav")


def test_tts_live_mimo_protocol(client, monkeypatch):
    """MiMo live：ready 声明 pcm16@24k；say → SSE 解码二进制帧 + sentence_end；
    end → round_end（无合成残留）。"""
    import psych_support_bot.api.routes.voice as voice_routes

    monkeypatch.setenv("VOICE_TTS_PROVIDER", "mimo")
    monkeypatch.setenv("VOICE_TTS_API_KEY", "k" * 8)
    from psych_support_bot.infra.config.settings import get_settings

    get_settings.cache_clear()
    monkeypatch.setattr(voice_routes, "_mimo_stream", lambda config, text, fmt: iter([b"\x01\x02", b"\x03\x04"]))

    with client.websocket_connect("/v1/voice/tts/live") as ws:
        ready = ws.receive_json()
        assert ready["type"] == "ready"
        assert ready["audio"] == {"format": "pcm", "sample_rate": 24000}
        ws.send_json({"type": "say", "text": "你好"})
        ws.send_json({"type": "end"})
        # 每个 SSE delta 一条 WS 二进制帧（前端 WebAudio 逐块入队）
        assert ws.receive_bytes() == b"\x01\x02"
        assert ws.receive_bytes() == b"\x03\x04"
        assert ws.receive_json()["type"] == "sentence_end"
        assert ws.receive_json()["type"] == "round_end"


# ---------------------------------------------------------------------------
# P1 反馈应答（backchannel）：列表 + 单条音频 + 缓存 + 校验
# ---------------------------------------------------------------------------


def test_backchannel_contract(client, monkeypatch):
    import httpx

    # 未配置 TTS：count 0、单条 503；越界先于配置检查 → 422
    assert client.get("/v1/voice/backchannel").json() == {"count": 0}
    assert client.get("/v1/voice/backchannel/0").status_code == 503
    assert client.get("/v1/voice/backchannel/99").status_code == 422

    monkeypatch.setenv("VOICE_TTS_PROVIDER", "openai")
    monkeypatch.setenv("VOICE_TTS_BASE_URL", "https://tts.example.com/v1")
    monkeypatch.setenv("VOICE_TTS_API_KEY", "k" * 8)
    from psych_support_bot.infra.config.settings import get_settings

    get_settings.cache_clear()

    calls = {"n": 0}

    def fake_post(url, **kw):
        calls["n"] += 1
        return httpx.Response(200, content=b"ID3bc", request=httpx.Request("POST", url))

    monkeypatch.setattr("psych_support_bot.infra.voice.adapter._client.post", fake_post)
    assert client.get("/v1/voice/backchannel").json() == {"count": 4}
    res = client.get("/v1/voice/backchannel/1")
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("audio/mpeg")
    client.get("/v1/voice/backchannel/1")  # 服务端文本缓存：第二次不打上游
    assert calls["n"] == 1
