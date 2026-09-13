"""SSE 流式对话路由契约测试（/v1/conversations/respond/stream）。

mock conversation_service.respond_stream，不发真实 LLM/网络请求。
覆盖：SSE 帧格式（data: json\n\n）、media_type、事件序列透传；
错误路径（生成器抛错 → 流中断）。
"""

import json
from typing import Any

import pytest
from fastapi.testclient import TestClient

from psych_support_bot.ai.schemas.messages import (
    ConversationResponse,
    GeneratedReply,
    RiskResult,
)
from psych_support_bot.app import create_app


@pytest.fixture()
def client():
    app = create_app()
    with TestClient(app) as test_client:
        yield test_client


def _final_response() -> ConversationResponse:
    return ConversationResponse(
        session_id="s1",
        mode="support",
        risk=RiskResult(risk_level="low", risk_types=[], needs_crisis_mode=False, reason=""),
        reply=GeneratedReply(text="嗯，我听到了。慢慢来。", style="support"),
        summary="",
    )


def test_respond_stream_sse_frames(client, monkeypatch):
    events: list[dict[str, Any]] = [
        {"type": "sentence", "text": "嗯，我听到了。"},
        {"type": "sentence", "text": "慢慢来。"},
        {"type": "final", "response": _final_response()},
    ]

    def fake_stream(payload, session):
        payload.user_id = "u-sse"
        yield from events

    monkeypatch.setattr(
        "psych_support_bot.services.conversation.conversation_service.respond_stream",
        fake_stream,
    )
    with client.stream(
        "POST",
        "/v1/conversations/respond/stream",
        json={"user_id": "u-sse", "message": "我心里有点乱"},
    ) as res:
        assert res.status_code == 200
        assert res.headers["content-type"].startswith("text/event-stream")
        body = "".join(res.iter_text())
    frames = [json.loads(line.removeprefix("data: ")) for line in body.splitlines() if line.startswith("data: ")]
    assert [f["type"] for f in frames] == ["sentence", "sentence", "final"]
    assert frames[0]["text"] == "嗯，我听到了。"
    assert frames[-1]["response"]["reply"]["text"] == "嗯，我听到了。慢慢来。"


# ---------------------------------------------------------------------------
# P1：整轮单会话 TTS WS（/v1/voice/tts/live）
# ---------------------------------------------------------------------------


def test_tts_live_ws_round(client, monkeypatch):
    """假上游 MiniMax 会话：task_start→started，say→audio+is_final，end→finished。"""
    import json as _json
    from uuid import uuid4

    # TTS 配置显式钉住：本用例此前不自带配置，靠 .env 或先行语音测试泄漏的
    # os.environ 才通过（pytest 文件名字典序它最先跑——长期潜伏的顺序耦合红灯）。
    from psych_support_bot.infra.config.settings import get_settings

    monkeypatch.setenv("VOICE_TTS_PROVIDER", "minimax")
    monkeypatch.setenv("VOICE_TTS_API_KEY", f"test-{uuid4().hex[:8]}")
    monkeypatch.delenv("VOICE_TTS_BASE_URL", raising=False)
    get_settings.cache_clear()

    class FakeMM:
        def __init__(self):
            self._out = []

        async def send(self, raw):
            ev = _json.loads(raw).get("event")
            if ev == "task_start":
                self._out.append(_json.dumps({"event": "task_started", "base_resp": {"status_code": 0}}))
            elif ev == "task_continue":
                self._out.append(
                    _json.dumps({"data": {"audio": "abcd"}, "is_final": True, "base_resp": {"status_code": 0}})
                )
            elif ev == "task_finish":
                self._out.append(_json.dumps({"event": "task_finished", "base_resp": {"status_code": 0}}))

        async def recv(self):
            # 真实 websockets.recv 会阻塞到有消息；空队列时轮询等待
            import asyncio as _a

            for _ in range(200):
                if self._out:
                    return self._out.pop(0)
                await _a.sleep(0.01)
            raise ConnectionError("timeout")

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return None

    import websockets

    monkeypatch.setattr(websockets, "connect", lambda *a, **k: FakeMM())

    with client.websocket_connect("/v1/voice/tts/live") as ws:
        ready = ws.receive_json()
        assert ready["type"] == "ready"
        assert ready["audio"]["format"] == "pcm"  # ② 协议：音频改 PCM 二进制帧直推
        ws.send_json({"type": "say", "text": "测试句。"})
        ws.send_json({"type": "end"})
        got = []
        binary = b""
        for _ in range(8):
            raw = ws.receive()
            if "bytes" in raw:
                binary += raw["bytes"]  # 二进制帧 = 裸 PCM（不再是 {"type":"audio","b64"} JSON）
                got.append("audio")
                continue
            msg = _json.loads(raw["text"])
            got.append(msg["type"])
            if msg["type"] == "round_end":
                break
        assert binary == bytes.fromhex("abcd")
        assert "sentence_end" in got and "round_end" in got


def test_respond_stream_empty_message_422(client):
    # 空 message：SSE 路由同样要求有效输入（ConversationRequest 校验）
    res = client.post(
        "/v1/conversations/respond/stream",
        json={"user_id": "u", "message": ""},
    )
    assert res.status_code == 422
