"""语音 I/O 路由（/v1/voice）。

- POST /transcribe：multipart 音频 → 转写文本。音频即转即弃（不落库、
  不写日志）；转写文本作为普通聊天消息的输入由前端走既有 /respond。
- POST /speak、/speak/stream：**已废弃**（对话朗读二选一收敛到 WS
  /tts/live，前端句队列已删除——运行时双路径的静默逐句跳过是不可观测
  的劣化，20260912 实证）。端点保留一个版本供旧前端兼容，勿新增调用。
- GET /status：前端探测（是否配置 STT/TTS），决定麦克风按钮显隐与朗读开关。

阻塞的上游调用走 asyncio.to_thread：适配层是同步 httpx / asyncio.run 包裹
的 WS 会话，事件循环内直调会卡死（minimax 路径还会 RuntimeError）。
"""

import asyncio
import contextlib
import json
import logging
import threading
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import Response, StreamingResponse

from psych_support_bot.api.auth import decode_access_token, require_auth
from psych_support_bot.infra.config.settings import get_settings
from psych_support_bot.infra.voice.adapter import (
    _MINIMAX_SAMPLE_RATE,
    MAX_AUDIO_BYTES,
    VoiceNotConfigured,
    VoiceProviderError,
    _mimo_stream,
    get_stt_config,
    get_tts_config,
    synthesize,
    synthesize_stream,
    transcribe,
    tts_media_type,
)
from psych_support_bot.infra.voice.protocol import (
    LiveAbort,
    LiveAudioFormat,
    LiveEnd,
    LiveError,
    LiveReady,
    LiveRoundEnd,
    LiveSay,
    LiveSentenceEnd,
    LiveServerEvent,
    LiveTtsProfile,
    parse_live_client_message,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/voice", tags=["voice"])

# 允许的音频格式（MediaRecorder 常见输出 + 常见手机录音格式）
_ALLOWED_AUDIO_TYPES = {
    "audio/webm",
    "audio/ogg",
    "audio/mpeg",
    "audio/mp3",
    "audio/mp4",
    "audio/m4a",
    "audio/wav",
    "audio/x-wav",
    "audio/wave",
    "audio/x-m4a",
    "audio/aac",
}

# TTS 输入上限（一次朗读一段回复，1000 字符远超三气泡上限）
_TTS_MAX_CHARS = 1000


# ---------------------------------------------------------------------------
# STT 故障看门狗（借鉴 WhisperLiveKit silent-backend guard：连续失败要吵闹，
# 不能让用户对着一台已经坏了的上游反复重说）。进程内计数，重启清零；
# degraded 标志带 TTL——上游恢复后无需成功请求也自动解除观察。
# ---------------------------------------------------------------------------

_STT_FAIL_DEGRADE_THRESHOLD = 3
_STT_DEGRADE_TTL_SECONDS = 300.0
_stt_fail_lock = threading.Lock()
_stt_fail_state = {"streak": 0, "last_fail": 0.0}


def _stt_fail_note(*, failed: bool) -> int:
    """更新连续失败计数，返回更新后的 streak（成功归零）。"""
    with _stt_fail_lock:
        if failed:
            _stt_fail_state["streak"] += 1
            _stt_fail_state["last_fail"] = time.monotonic()
        else:
            _stt_fail_state["streak"] = 0
        return _stt_fail_state["streak"]


def _stt_degraded() -> bool:
    with _stt_fail_lock:
        return (
            _stt_fail_state["streak"] >= _STT_FAIL_DEGRADE_THRESHOLD
            and time.monotonic() - _stt_fail_state["last_fail"] < _STT_DEGRADE_TTL_SECONDS
        )


def _reset_stt_fail_state_for_tests() -> None:
    with _stt_fail_lock:
        _stt_fail_state["streak"] = 0
        _stt_fail_state["last_fail"] = 0.0


@router.get("/status")
def voice_status(request: Request, _sub: str = Depends(require_auth)) -> dict[str, bool]:
    return {
        "stt": get_stt_config() is not None,
        "tts": get_tts_config() is not None,
        # 上游连续失败观察中：前端进免提前给出「可能不可用」预期（麦克风不隐藏，
        # 保留一次试探即恢复归零的路径）
        "stt_degraded": _stt_degraded(),
    }


@router.post("/transcribe")
async def transcribe_audio(request: Request, file: UploadFile, _sub: str = Depends(require_auth)) -> dict[str, Any]:
    content_type = (file.content_type or "").split(";")[0].strip().lower()
    if content_type and content_type not in _ALLOWED_AUDIO_TYPES:
        raise HTTPException(status_code=422, detail=f"Unsupported audio type: {content_type}")
    audio = await file.read()
    if not audio:
        raise HTTPException(status_code=422, detail="Empty audio file")
    if len(audio) > MAX_AUDIO_BYTES:
        raise HTTPException(status_code=413, detail="Audio file too large (max 25MB)")
    filename = file.filename or "audio.webm"
    started = time.monotonic()
    try:
        text = await asyncio.to_thread(transcribe, audio, filename)
    except VoiceNotConfigured as exc:
        # 配置性未配置 ≠ 上游故障：不计入看门狗 streak
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except VoiceProviderError as exc:
        streak = _stt_fail_note(failed=True)
        # 连续失败升格为 ERROR（WhisperLiveKit 教训：静默故障只打 warning，
        # 用户看到的是「永远转不出来」）
        log = logger.error if streak >= 2 else logger.warning
        log("Voice transcribe failed (streak=%d): %s", streak, exc)
        raise HTTPException(status_code=502, detail=f"Transcription failed: {exc}") from exc
    _stt_fail_note(failed=False)
    # 服务端 STT 段耗时（前端 /turn_metrics 上报的是含网络的全链差值，
    # 两行日志对账即可切分出「网络+排队」与「上游合成」各占多少）
    config = get_stt_config()
    logger.info(
        "VOICE_STT dur=%.3fs bytes=%d provider=%s",
        time.monotonic() - started,
        len(audio),
        config.provider if config else "?",
    )
    return {"text": text}


# 语音回合分阶段耗时（纯数值打点：不含文本/音频内容，隐私零增量）
_TURN_METRIC_KEYS = (
    "speech_end_to_stt_ms",
    "stt_to_first_sentence_ms",
    "first_sentence_to_final_ms",
    "final_to_first_audio_ms",
    "speech_end_to_first_audio_ms",
)


@router.post("/turn_metrics")
async def turn_metrics(request: Request, payload: dict[str, Any], _sub: str = Depends(require_auth)) -> dict[str, bool]:
    """前端语音回合阶段耗时上报（WhisperLiveKit remaining_time_* 分段思路）。

    只收白名单数值键，异常值（≤0 或 >10min）静默丢弃；单行 INFO 日志
    （VOICE_TURN_METRICS {...}）供离线聚合归因，不落库。
    """
    stages: dict[str, float | bool] = {}
    for key in _TURN_METRIC_KEYS:
        value = payload.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and 0 < float(value) < 600_000:
            stages[key] = round(float(value))
    if isinstance(payload.get("tts_enabled"), bool):
        stages["tts_enabled"] = payload["tts_enabled"]
    if not stages:
        raise HTTPException(status_code=422, detail="No valid turn metrics")
    logger.info("VOICE_TURN_METRICS %s", json.dumps(stages, sort_keys=True))
    return {"ok": True}


@router.post("/speak")
@router.post("/speak", deprecated=True)
async def speak_text(request: Request, payload: dict[str, Any], _sub: str = Depends(require_auth)) -> Response:
    """文本 → audio（已废弃）：对话朗读已收敛到 WS /tts/live（二选一，
    见 VOICE_DECISIONS.md D6）。仅为旧前端保留一个版本，勿新增调用。"""
    text = str(payload.get("text") or "").strip()
    if not text:
        raise HTTPException(status_code=422, detail="Empty text")
    if len(text) > _TTS_MAX_CHARS:
        raise HTTPException(status_code=422, detail=f"Text too long (max {_TTS_MAX_CHARS} chars)")
    try:
        audio = await asyncio.to_thread(synthesize, text)
    except VoiceNotConfigured as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except VoiceProviderError as exc:
        logger.warning("Voice speak failed: %s", exc)
        raise HTTPException(status_code=502, detail=f"Speech synthesis failed: {exc}") from exc
    return Response(content=audio, media_type=tts_media_type())


@router.post("/speak/stream")
@router.post("/speak/stream", deprecated=True)
async def speak_stream(request: Request, payload: dict[str, Any], _sub: str = Depends(require_auth)):
    """文本 → mp3 流（chunked）（已废弃）：同 /speak，仅为旧前端保留。

    感知延迟 ≈ 上游首块到达时间（MiniMax 实测 ~0.5s），远低于整段合成。
    首个字节前的配置/校验错误仍返回 JSON 错误码；流出后中途失败只能
    截断（记日志），前端按已收音频播放。
    """
    text = str(payload.get("text") or "").strip()
    if not text:
        raise HTTPException(status_code=422, detail="Empty text")
    if len(text) > _TTS_MAX_CHARS:
        raise HTTPException(status_code=422, detail=f"Text too long (max {_TTS_MAX_CHARS} chars)")
    # 配置校验前置：流开始后无法再改状态码
    if get_tts_config() is None:
        raise HTTPException(status_code=503, detail="Voice TTS is not configured")

    def _gen():
        try:
            yield from synthesize_stream(text)
        except VoiceProviderError as exc:
            # 流已开始：状态码不可改，截断表达（前端播放已收到的部分）
            logger.warning("Voice speak stream interrupted: %s", exc)

    return StreamingResponse(_gen(), media_type=tts_media_type())


# ---------------------------------------------------------------------------
# P1 反馈应答（backchannel）：思考间隙的在场感短句
# 前端进免提后一次性预取为 blob URL，thinking 死区概率播放（≤0.8s）——
# 把「说完话等回复」的 2-4s 空白变成「对方在消化」。合成走 synthesize() 的
# LRU+TTL 缓存：同一音色同一短语只打一次上游，过期再补，成本可忽略。
# ---------------------------------------------------------------------------

_BACKCHANNEL_PHRASES = ("嗯——", "嗯嗯", "好，我在听", "哦……")


@router.get("/backchannel")
def backchannel_list(_sub: str = Depends(require_auth)) -> dict[str, int]:
    """应答条目数（前端据此预取；未配置 TTS 返回 0，前端静默跳过）。"""
    return {"count": len(_BACKCHANNEL_PHRASES) if get_tts_config() is not None else 0}


@router.get("/backchannel/{index}")
async def backchannel_audio(index: int, _sub: str = Depends(require_auth)) -> Response:
    """单条应答音频（mp3）。越界 422；未配置 503；上游失败 502（前端容错为无应答）。"""
    if not 0 <= index < len(_BACKCHANNEL_PHRASES):
        raise HTTPException(status_code=422, detail="Backchannel index out of range")
    try:
        audio = await asyncio.to_thread(synthesize, _BACKCHANNEL_PHRASES[index])
    except VoiceNotConfigured as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except VoiceProviderError as exc:
        logger.warning("Voice backchannel failed: %s", exc)
        raise HTTPException(status_code=502, detail=f"Backchannel synthesis failed: {exc}") from exc
    return Response(content=audio, media_type=tts_media_type())


# ---------------------------------------------------------------------------
# P1：整轮单会话全双工 TTS（WS）
# 浏览器开一条 WS，LLM 流式产出的每个句子以 {"type":"say"} 推入；服务器用
# 同一个 MiniMax bidi 会话逐句 task_continue。音频走 **PCM(32k mono s16le)
# 二进制帧直推**（ready 消息声明格式），浏览器 WebAudio 队列首块即播——
# 不再等整句 mp3 合成完才起播。控制面仍是 JSON（text 帧）：sentence_end
# 分句边界、round_end 轮终点；{"type":"abort"} → task_cancel。鉴权走 query
# token（浏览器 WS 无法自定义 Header）。音频仍经服务器（key 不出服务端）。
# ---------------------------------------------------------------------------

ws_router = APIRouter(prefix="/v1/voice", tags=["voice"])

# MiniMax live 上游 recv 空闲上限：句间合成块间隔实测 <1s，卡 30s 必是上游
# 挂起。取 30s 略大于前端 ttsLiveEndRound 的 25s 收束硬上限——前端先收束
# 兜底，服务端随后自然超时关连（旧值 120s 会把前端假死窗口拉满）。
_TTS_LIVE_UPSTREAM_RECV_TIMEOUT = 30.0


async def _send_event(websocket: WebSocket, event: LiveServerEvent) -> None:
    """控制面事件统一下发口：形状由 protocol.py 模型保证（前端契约的唯一权威）。"""
    await websocket.send_json(event.model_dump())


async def _tts_live_mimo(websocket: WebSocket, config, text_q: asyncio.Queue, read_client) -> None:
    """MiMo live 管道：无上游 WS，每条 say 起一个 HTTP SSE 合成。

    delta 音频（pcm16 24kHz）解码后即刻以二进制帧直推浏览器（前端 WebAudio
    队列首块即播）；每句音频流末尾补发 sentence_end——句边界即客户端 say
    粒度（我们的切句），比 MiniMax 的攒句更可控。end 语义：say 队列排空即
    round_end（MiMo 无合成残留）。限流 100 RPM：句粒度请求，多用户场景
    需观察配额，必要时按气泡段合并请求。
    """
    import threading
    from contextlib import suppress

    # 复刻模型（兼容模式流式）整句合成完才返回：首包预算同步放宽，
    # 前端「无首音收束」按此放宽（缺省/预置音色维持 6s 快收束还麦）
    tts_profile = LiveTtsProfile(first_audio_timeout_ms=20_000) if "voiceclone" in (config.model or "") else None
    await _send_event(websocket, LiveReady(audio=LiveAudioFormat(sample_rate=24000), tts=tts_profile))
    loop = asyncio.get_running_loop()

    async def pump_say(text: str) -> None:
        q: asyncio.Queue = asyncio.Queue()

        def worker() -> None:
            try:
                for chunk in _mimo_stream(config, text, "pcm16"):
                    loop.call_soon_threadsafe(q.put_nowait, chunk)
            except BaseException as exc:  # noqa: BLE001 —— 线程边界：异常作为队列结果上抛
                loop.call_soon_threadsafe(q.put_nowait, exc)
            finally:
                loop.call_soon_threadsafe(q.put_nowait, None)

        threading.Thread(target=worker, daemon=True).start()
        while True:
            item = await q.get()
            if item is None:
                break
            if isinstance(item, VoiceProviderError):
                raise item
            await websocket.send_bytes(item)

    reader: asyncio.Task | None = None
    try:
        reader = asyncio.ensure_future(read_client())
        while True:
            item = await text_q.get()
            if item == "__abort__":
                return
            if item is None:
                await _send_event(websocket, LiveRoundEnd())
                return
            try:
                await pump_say(item)
                await _send_event(websocket, LiveSentenceEnd())
            except VoiceProviderError as exc:
                logger.warning("TTS live mimo: %s", exc)
                await _send_event(websocket, LiveError(detail=str(exc)[:200]))
                await _send_event(websocket, LiveRoundEnd())
                return
    except WebSocketDisconnect:
        logger.info("TTS live mimo: client disconnected")
    finally:
        if reader is not None:
            reader.cancel()
        with suppress(Exception):
            await websocket.close()


@ws_router.websocket("/tts/live")
async def tts_live(websocket: WebSocket, token: str = Query(default="")):
    settings = get_settings()
    if settings.auth_enabled:
        try:
            decode_access_token(token)
        except Exception:  # noqa: BLE001 —— 任何签发/解码/过期错误一律拒绝（fail-closed）
            await websocket.close(code=4401)
            return

    config = get_tts_config()
    await websocket.accept()
    if config is None or config.provider not in {"minimax", "mimo"}:
        logger.warning("TTS live: TTS not configured, closing")
        await _send_event(websocket, LiveError(detail="TTS not configured"))
        await websocket.close()
        return

    text_q: asyncio.Queue = asyncio.Queue()

    async def read_client() -> None:
        try:
            while True:
                raw = await websocket.receive_text()
                try:
                    msg = parse_live_client_message(raw)
                except ValueError:
                    continue  # 垃圾帧不杀会话（协议边界拒绝，行为同历史）
                if isinstance(msg, LiveSay):
                    text = msg.text.strip()
                    if text:
                        await text_q.put(text)
                elif isinstance(msg, LiveEnd):
                    await text_q.put(None)
                    return
                elif isinstance(msg, LiveAbort):
                    await text_q.put("__abort__")
                    return
        except Exception:  # noqa: BLE001 —— 断连/坏帧不杀会话；finally 唤醒等待者
            pass
        finally:
            await text_q.put(None)

    if config.provider == "mimo":
        # MiMo 无上游 WS：HTTP SSE 合成，句边界即 say 粒度（我们自己的切句）
        await _tts_live_mimo(websocket, config, text_q, read_client)
        return

    import websockets as mm_lib

    # 后台任务登记表：ensure_future 的 task 只被事件循环弱引用（RUF006），
    # 且异常无人 await 会在 GC 时报 "Task exception was never retrieved"。
    # finally 统一 cancel + 吞异常（终态下任务死于断连/上游关闭是常态）。
    live_tasks: set[asyncio.Task] = set()

    try:
        async with mm_lib.connect(
            config.ws_url,
            additional_headers={"Authorization": f"Bearer {config.api_key}"},
            open_timeout=10,
            close_timeout=5,
        ) as mmws:
            logger.info("TTS live: upstream connected")
            # 音频面改 PCM 直推：浏览器收到二进制帧即入 WebAudio 播放队列，
            # 首块（~0.1-0.3s 音频）到达就能起播，不等整句合成完
            await _send_event(websocket, LiveReady(audio=LiveAudioFormat(sample_rate=_MINIMAX_SAMPLE_RATE)))
            # 上游任务开启（音色与整段合成路径一致；PCM 无码率概念）
            await mmws.send(
                json.dumps(
                    {
                        "event": "task_start",
                        "model": config.model,
                        **({"language_boost": config.language_boost} if config.language_boost else {}),
                        "voice_setting": {"voice_id": config.voice, "speed": 1.05, "vol": 1, "pitch": 0},
                        "audio_setting": {
                            "sample_rate": _MINIMAX_SAMPLE_RATE,
                            "format": "pcm",
                            "channel": 1,
                        },
                    }
                )
            )
            started = json.loads(await asyncio.wait_for(mmws.recv(), timeout=15))
            # 网关可能重复推 connected_success（实测每次 task_start 响应前
            # 都有一条）；跳过直到看到 task_started 或明确失败。
            while (
                started.get("event") not in ("task_started", "task_failed")
                and started.get("base_resp", {}).get("status_code", 0) == 0
            ):
                started = json.loads(await asyncio.wait_for(mmws.recv(), timeout=15))
            if started.get("event") != "task_started":
                base = started.get("base_resp") or {}
                logger.warning("TTS live task_start failed: %s", base)
                await _send_event(websocket, LiveError(detail=f"task_start failed: {base.get('status_code')}"))
                await _send_event(websocket, LiveRoundEnd())
                return
            # 客户端读取循环与上游音频泵并行：say 逐句喂入，音频块实时回推
            reader_task = asyncio.ensure_future(read_client())
            live_tasks.add(reader_task)
            reader_task.add_done_callback(live_tasks.discard)

            async def client_to_mm() -> None:
                # 客户端句子 → MiniMax task_continue；end → task_finish；
                # abort → task_cancel
                sent_finish = False
                while True:
                    item = await text_q.get()
                    if item == "__abort__":
                        await mmws.send(json.dumps({"event": "task_cancel"}))
                        return
                    if item is None:
                        if not sent_finish:
                            await mmws.send(json.dumps({"event": "task_finish"}))
                            sent_finish = True
                        return
                    await mmws.send(json.dumps({"event": "task_continue", "text": item}))

            ct = asyncio.ensure_future(client_to_mm())
            live_tasks.add(ct)
            ct.add_done_callback(live_tasks.discard)
            while True:
                raw = await asyncio.wait_for(mmws.recv(), timeout=_TTS_LIVE_UPSTREAM_RECV_TIMEOUT)
                msg = json.loads(raw)
                base = msg.get("base_resp") or {}
                status = base.get("status_code", 0)
                if status != 0:
                    logger.warning("TTS live upstream error %s: %s", status, base.get("status_msg"))
                    # 错误显式下发（round_end 前）：前端据此把剩余句子转投 HTTP
                    # 队列续读——上游半途故障不再表现为"音频静默消失"
                    await _send_event(websocket, LiveError(detail=f"upstream {status}: {base.get('status_msg')}"))
                    await _send_event(websocket, LiveRoundEnd())
                    return
                event = msg.get("event")
                data = msg.get("data") or {}
                if data.get("audio"):
                    # 二进制帧 = 裸 PCM 字节（s16le mono 32k），前端 binaryType
                    # 设 arraybuffer 后零转换直接入 WebAudio 队列
                    await websocket.send_bytes(bytes.fromhex(data["audio"]))
                if msg.get("is_final"):
                    # 句级边界：前端以此切分播放单元（sentence_start/end 事件
                    # 名沿用语义）。会话不终态，下一句 task_continue 继续喂。
                    await _send_event(websocket, LiveSentenceEnd())
                if event in ("task_finished", "task_failed"):
                    await _send_event(websocket, LiveRoundEnd())
                    return
    except WebSocketDisconnect:
        logger.info("TTS live: client disconnected")
    except Exception as exc:
        logger.warning("TTS live session ended: %s", exc, exc_info=True)
    finally:
        pending = list(live_tasks)
        for task in pending:
            task.cancel()
        # 终态下任务死于断连/上游关闭是常态：吞掉取消与残余异常
        await asyncio.gather(*pending, return_exceptions=True)
        with contextlib.suppress(Exception):
            await websocket.close()
