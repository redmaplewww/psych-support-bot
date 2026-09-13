"""TTS live 全双工管道（/v1/voice/tts/live）控制面协议 —— 前后端共享契约的唯一权威定义。

- 服务端收发走本模块模型（routes/voice.py），非法文本帧在边界被拒（跳过）。
- 前端镜像：static/js/voice/protocol.js（事件名常量与本模型同名同源）。
- 机读 Schema：scripts/export_voice_protocol.py 导出 docs/technical/voice-protocol.schema.json，
  前端特征测试（tests/frontend/protocol.test.mjs）据此交叉校验。
- 人类文档：docs/technical/VOICE_PROTOCOL.md（事件时序、降级契约、音频面帧格式）。

音频面不经本协议建模：二进制帧 = 裸 PCM（s16le mono），采样率由 ready 事件声明，
浏览器 WebAudio 队列零转换直播。

兼容性约定：未知 type 拒绝（前端未知事件一律忽略，方向相反——客户端对
服务端新事件免疫）；多余字段忽略（旧前端多发字段不被新服务端拒，前向兼容）；
删除/改义事件属破坏性变更，需前端同批发布。
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field, TypeAdapter, ValidationError

# ---------------------------------------------------------------------------
# 客户端 → 服务端（JSON 文本帧）
# ---------------------------------------------------------------------------


class LiveSay(BaseModel):
    """合成一句：LLM 流式句直灌，或整轮全文（ttsLiveRound）。空文本由服务端忽略。"""

    type: Literal["say"] = "say"
    text: str


class LiveEnd(BaseModel):
    """本轮结束：say 队列排空后关闭上游合成会话（MiniMax task_finish）。"""

    type: Literal["end"] = "end"


class LiveAbort(BaseModel):
    """打断：立即取消上游合成（MiniMax task_cancel）；前端同时掐掉本地连接，
    在途残余帧由连接代次守卫作废。"""

    type: Literal["abort"] = "abort"


LiveClientMessage = Annotated[LiveSay | LiveEnd | LiveAbort, Field(discriminator="type")]


# ---------------------------------------------------------------------------
# 服务端 → 客户端（JSON 文本帧；二进制帧为 PCM 音频块）
# ---------------------------------------------------------------------------


class LiveAudioFormat(BaseModel):
    """音频面声明：format=pcm（裸 PCM s16le mono），sample_rate 由供应商决定
    （MiniMax 32k / MiMo 24k）。前端按声明值重采样到设备采样率。"""

    format: Literal["pcm"] = "pcm"
    sample_rate: int


class LiveTtsProfile(BaseModel):
    """TTS 延迟画像：前端据 first_audio_timeout_ms 放宽「无首音」收束上限。

    预置音色首包 ~1-2s（6s 收束够）；音色复刻为兼容模式流式——整句合成完
    才返回单块，首包 5-20s，6s 会掐掉整轮（20260912 实证：只读到最短一句）。
    可选字段：旧前端忽略（前向兼容约定）。"""

    first_audio_timeout_ms: int


class LiveReady(BaseModel):
    """会话就绪 + 音频面声明。此后二进制帧即音频块，可首块即播。"""

    type: Literal["ready"] = "ready"
    audio: LiveAudioFormat
    tts: LiveTtsProfile | None = None


class LiveSentenceEnd(BaseModel):
    """当前句音频边界：MiMo=我们的 say 粒度（每句合成流末尾）；
    MiniMax=is_final（服务端攒句）。前端以此驱动字幕铺字与下一句入队。"""

    type: Literal["sentence_end"] = "sentence_end"


class LiveRoundEnd(BaseModel):
    """本轮终点：音频面此后不再有帧，前端可安全收束（等本地队列播完撤黄框）。
    上游错误路径会在 error 之后补发本事件，保证前端总能收到终态。"""

    type: Literal["round_end"] = "round_end"


class LiveError(BaseModel):
    """上游故障（配额/风控/挂起）。前端契约：把剩余未读句子转投 HTTP 句队列
    续读（后半段不静默消失），并弃用本页 WS 通道（TTS_LIVE.failed = true）。"""

    type: Literal["error"] = "error"
    detail: str


LiveServerEvent = Annotated[
    LiveReady | LiveSentenceEnd | LiveRoundEnd | LiveError,
    Field(discriminator="type"),
]


# ---------------------------------------------------------------------------
# 解析 / Schema 导出
# ---------------------------------------------------------------------------

_client_adapter: TypeAdapter = TypeAdapter(LiveClientMessage)


def parse_live_client_message(raw: str) -> LiveSay | LiveEnd | LiveAbort:
    """解析客户端文本帧。非法 JSON、未知 type、字段违规一律抛 ValueError
    （调用方静默跳过——与历史行为一致：垃圾帧不杀会话）。"""
    try:
        return _client_adapter.validate_json(raw)
    except ValidationError as exc:
        raise ValueError(f"invalid live client message: {exc.errors(include_url=False)[:3]}") from exc


def export_json_schema() -> dict:
    """双端契约的机读形态（scripts/export_voice_protocol.py 落盘到 docs/）。"""
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "psych-support-bot /v1/voice/tts/live protocol",
        "description": "client_message=浏览器→服务端 JSON 文本帧；server_event=服务端→浏览器 JSON 文本帧。二进制帧为裸 PCM 音频块（s16le mono，采样率见 ready 事件），不在此建模。",
        "client_message": TypeAdapter(LiveClientMessage).json_schema(),
        "server_event": TypeAdapter(LiveServerEvent).json_schema(),
    }
