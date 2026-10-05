// /v1/voice/tts/live 控制面协议常量 —— 服务端权威定义：
// src/psych_support_bot/infra/voice/protocol.py（同名同源，勿单边改动）。
// 机读 Schema：docs/technical/voice-protocol.schema.json（协议变更后重跑
// scripts/export_voice_protocol.py）；人类文档：docs/technical/VOICE_PROTOCOL.md。
// 交叉校验：tests/frontend/protocol.test.mjs 读取 schema 文件比对本文常量。

export const CLIENT = { SAY: 'say', END: 'end', ABORT: 'abort' };
export const SERVER = { READY: 'ready', SENTENCE_START: 'sentence_start', SENTENCE_END: 'sentence_end', ROUND_END: 'round_end', ERROR: 'error' };

// 客户端 → 服务端文本帧构造器
export const say = (text, round_id = '', sentence_id = 0) => ({ type: CLIENT.SAY, text, round_id, sentence_id });
export const end = () => ({ type: CLIENT.END });
export const abort = () => ({ type: CLIENT.ABORT });

// 服务端 → 客户端事件构造器（仅文档/测试用；运行时由服务端下发）
export const ready = (sampleRate, tts) => ({
  type: SERVER.READY,
  audio: { format: 'pcm', sample_rate: sampleRate },
  ...(tts ? { tts } : {}), // 可选 TTS 延迟画像 {first_audio_timeout_ms}：复刻音色下发，预置音色省略
});
export const sentenceStart = (round_id = '', sentence_id = 0) => ({ type: SERVER.SENTENCE_START, round_id, sentence_id });
export const sentenceEnd = (round_id = '', sentence_id = 0) => ({ type: SERVER.SENTENCE_END, round_id, sentence_id });
export const roundEnd = () => ({ type: SERVER.ROUND_END });
export const error = (detail) => ({ type: SERVER.ERROR, detail });
