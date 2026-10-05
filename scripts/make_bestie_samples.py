"""生成「闺蜜音色」（MiniMax Chinese (Mandarin)_Warm_Bestie）的复刻参考样本。

用法（需要 MiniMax 凭证）：
    MINIMAX_API_KEY=... MINIMAX_GROUP_ID=... .venv/bin/python scripts/make_bestie_samples.py [输出文件名]

产出：一段多情感、多语速的连续独白 mp3（默认 data/voice/bestie_ref.mp3），
作为小米 MiMo-V2.5-TTS-VoiceClone 的内联参考音频（≤10MB base64）。
输出强制落在 data/voice/ 内（只接受文件名，防路径穿越）。

注意：MiniMax t2a_v2 的响应字段以官方文档为准；脚本失败时打印响应顶层键，
便于对照当前 API 形状修正。
"""

from __future__ import annotations

import base64
import os
import sys
import time
from pathlib import Path

import httpx

API = "https://api.minimax.cn/v1/t2a_v2"
VOICE_ID = "Chinese (Mandarin)_Warm_Bestie"
MODEL = "speech-2.8-turbo"
OUT_DIR = Path("data/voice")

# 样本文本：覆盖温和陪伴、轻快安慰、低速安抚三类语气，让复刻模型学到
# 「闺蜜感」的音色而不只是单一语调。总时长目标 ~60-90s（足够建模，远小于 10MB）。
PARAGRAPHS = [
    "你好呀，我一直在呢。今天过得怎么样？不管是什么样的心情，都可以慢慢讲给我听。",
    "嗯，我听到了。能在意自己的感受，说明你一直在认真生活，这本身就很了不起。",
    "别急，我们不赶时间。你愿意说的时候再说，我就在这里陪着你。",
    "累的时候就允许自己累一会儿吧，不是每件事都要马上想明白的。",
    "你已经做得很好了，真的。剩下的，我们一起慢慢来，好不好？",
]


def synth_one(client: httpx.Client, api_key: str, group_id: str, text: str) -> bytes:
    # 新版平台 key 免 GroupId；留空则不带查询参数
    url = f"{API}?GroupId={group_id}" if group_id else API
    resp = client.post(
        url,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={
            "model": MODEL,
            "text": text,
            "stream": False,
            "voice_setting": {
                "voice_id": VOICE_ID,
                "speed": 0.95,
                "vol": 1.0,
                "pitch": 0,
            },
            "audio_setting": {
                "sample_rate": 32000,
                "bitrate": 128000,
                "format": "mp3",
                "channel": 1,
            },
        },
        timeout=60.0,
    )
    resp.raise_for_status()
    data = resp.json()
    audio_hex = ((data.get("data") or {}).get("audio")) or ""
    if not audio_hex:
        keys = list(data.keys())
        raise SystemExit(
            f"响应中没有 data.audio（顶层键：{keys}）。"
            "请对照 MiniMax 当前文档修正本脚本的请求/解析字段。"
            f" 原始 base_resp：{data.get('base_resp')}"
        )
    return bytes.fromhex(audio_hex)


def main() -> None:
    api_key = os.environ.get("MINIMAX_API_KEY", "").strip()
    group_id = os.environ.get("MINIMAX_GROUP_ID", "").strip()  # 新版 key 可留空
    if not api_key:
        raise SystemExit("需要环境变量 MINIMAX_API_KEY")

    # 只接受文件名（basename），强制输出在 data/voice/ 内：防路径穿越
    name = Path(sys.argv[1] if len(sys.argv) > 1 else "bestie_ref.mp3").name
    if not name.endswith(".mp3"):
        name += ".mp3"
    out_path = OUT_DIR / name

    chunks: list[bytes] = []
    with httpx.Client() as client:
        for i, text in enumerate(PARAGRAPHS, 1):
            t0 = time.perf_counter()
            mp3 = synth_one(client, api_key, group_id, text)
            chunks.append(mp3)
            print(f"[{i}/{len(PARAGRAPHS)}] {len(mp3)} bytes · {time.perf_counter() - t0:.1f}s · {text[:18]}…")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    blob = b"".join(chunks)  # mp3 帧拼接：作为复刻参考样本可被解码即可
    out_path.write_bytes(blob)
    print(f"\n已写出 {out_path}（{len(blob)} bytes，base64 后 {len(base64.b64encode(blob))} bytes，上限 10MB）")
    print(f"下一步：VOICE_TTS_MODEL=mimo-v2.5-tts-voiceclone  VOICE_TTS_VOICE={out_path}")


if __name__ == "__main__":
    main()
