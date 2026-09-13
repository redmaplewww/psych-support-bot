"""TTS live 控制面协议模型单测（infra/voice/protocol.py）。

保证：合法帧可解析、垃圾帧在边界被拒（ValueError→调用方跳过）、
出站事件序列化形状与前端历史线上格式逐字一致（协议固化≠协议变更）。
"""

import json

import pytest

from psych_support_bot.infra.voice.protocol import (
    LiveError,
    LiveReady,
    LiveRoundEnd,
    LiveSentenceEnd,
    export_json_schema,
    parse_live_client_message,
)

# ---------------------------------------------------------------------------
# 入站（客户端 → 服务端）
# ---------------------------------------------------------------------------


def test_parse_say_end_abort() -> None:
    say = parse_live_client_message('{"type": "say", "text": "你好呀"}')
    assert say.type == "say" and say.text == "你好呀"
    assert parse_live_client_message('{"type": "end"}').type == "end"
    assert parse_live_client_message('{"type": "abort"}').type == "abort"


@pytest.mark.parametrize(
    "raw",
    [
        "not json",  # 非法 JSON
        '{"type": "unknown"}',  # 未知 type：契约外事件，拒绝（前端旧版本不发）
        '{"type": "say"}',  # say 缺 text
        '{"type": "say", "text": 123}',  # text 非字符串
    ],
)
def test_parse_rejects_garbage(raw: str) -> None:
    with pytest.raises(ValueError):
        parse_live_client_message(raw)


def test_parse_ignores_extra_fields() -> None:
    """多余字段忽略（前向兼容）：旧前端多发字段不被新服务端拒。"""
    assert parse_live_client_message('{"type": "end", "extra": true}').type == "end"


def test_parse_say_empty_text_is_valid_frame() -> None:
    """空文本帧可解析——「忽略空 say」是服务端行为层语义（read_client strip 后跳过），
    不是协议层违规，与历史 str(text or "").strip() 行为一致。"""
    say = parse_live_client_message('{"type": "say", "text": "  "}')
    assert say.type == "say"


# ---------------------------------------------------------------------------
# 出站（服务端 → 客户端）：线格式逐字锁定
# ---------------------------------------------------------------------------


def test_outgoing_wire_shapes_unchanged() -> None:
    """前端（含线上旧版本）按 type 分派：字段名/形状漂移=全体用户静默故障。"""
    ready = LiveReady(audio={"format": "pcm", "sample_rate": 24000}).model_dump()
    assert ready == {
        "type": "ready",
        "audio": {"format": "pcm", "sample_rate": 24000},
        "tts": None,  # 预置音色：无延迟画像，前端按 null 走 6s 快收束
    }
    clone_ready = LiveReady(
        audio={"format": "pcm", "sample_rate": 24000},
        tts={"first_audio_timeout_ms": 20000},
    ).model_dump()
    assert clone_ready["tts"] == {"first_audio_timeout_ms": 20000}
    assert LiveSentenceEnd().model_dump() == {"type": "sentence_end"}
    assert LiveRoundEnd().model_dump() == {"type": "round_end"}
    assert LiveError(detail="boom").model_dump() == {"type": "error", "detail": "boom"}


def test_ready_accepts_prebuilt_audio_dict() -> None:
    """audio 以 dict 传入（路由层就地构造）自动转模型。"""
    ready = LiveReady(audio={"format": "pcm", "sample_rate": 32000})
    assert ready.audio.sample_rate == 32000


# ---------------------------------------------------------------------------
# Schema 导出（前端交叉校验的数据源）
# ---------------------------------------------------------------------------


def test_exported_schema_covers_all_events() -> None:
    schema = export_json_schema()
    assert schema["$schema"].startswith("https://json-schema.org")
    client_types = json.dumps(schema["client_message"])
    server_types = json.dumps(schema["server_event"])
    for t in ("say", "end", "abort"):
        assert f'"const": "{t}"' in client_types
    for t in ("ready", "sentence_end", "round_end", "error"):
        assert f'"const": "{t}"' in server_types
    # 文档描述字段在前（给读 schema 的人），事件模型在后
    assert "client_message" in schema and "server_event" in schema
