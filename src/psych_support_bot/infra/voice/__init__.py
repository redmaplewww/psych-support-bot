"""语音 I/O 适配层（STT / TTS）。

供应商无关：STT 支持 openai（multipart /audio/transcriptions）与 dots
（chat completions + audio_url，base64 data URI 内联）双模式；TTS 走
OpenAI 兼容 /audio/speech 与 MiniMax 双向流式 WS。配置与主 LLM 完全
分离（VOICE_STT_* / VOICE_TTS_*）。
"""

from psych_support_bot.infra.voice.adapter import (
    MAX_AUDIO_BYTES,
    SttConfig,
    TtsConfig,
    VoiceNotConfigured,
    VoiceProviderError,
    get_stt_config,
    get_tts_config,
    synthesize,
    transcribe,
    validate_public_http_url,
)

__all__ = [
    "MAX_AUDIO_BYTES",
    "SttConfig",
    "TtsConfig",
    "VoiceNotConfigured",
    "VoiceProviderError",
    "get_stt_config",
    "get_tts_config",
    "synthesize",
    "transcribe",
    "validate_public_http_url",
]
