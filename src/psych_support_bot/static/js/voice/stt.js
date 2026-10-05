/**
 * STT live WebSocket 客户端（/v1/voice/stt/live）。
 *
 * 职责：管理 STT WebSocket 连接，发送 PCM 音频帧，接收 partial/final 转写结果。
 * 与 live.js（TTS）对称但独立——职责不同、生命周期不同、可能用不同供应商。
 *
 * 协议：
 * - Client → Server：binary PCM 帧（16kHz mono s16le）、text {"type":"end"}、text {"type":"abort"}
 * - Server → Client：ready / stt_partial / stt_final / stt_error
 *
 * 前端镜像：static/js/voice/protocol.js 中的 STT 常量。
 */

/* global WebSocket */

export function createSttLive(deps) {
  const { wsUrl, timers, debug, onReady, onPartial, onFinal, onError } = deps;

  let ws = null;
  let connecting = null;
  let generation = 0;

  function sttWsUrl() {
    return wsUrl('/stt/live');
  }

  /**
   * 确保 STT WebSocket 连接就绪。复用 live.js 的连接复用模式：
   * - readyState=1 直接返回
   * - readyState=0 + connecting 存在则返回同一个 promise
   * - 否则新建连接
   */
  function connect() {
    const gen = generation;
    if (ws && ws.readyState === 1) return Promise.resolve(ws);
    if (connecting) return connecting;

    const socket = new WebSocket(sttWsUrl());
    socket.binaryType = 'arraybuffer';
    ws = socket;

    connecting = new Promise((resolve, reject) => {
      const timeout = timers.setTimeout(() => {
        reject(new Error('STT WS open timeout'));
      }, 5000);

      socket.onopen = () => {
        timers.clearTimeout(timeout);
        if (gen !== generation) { try { socket.close(); } catch (_) { /* 已关 */ } return; }
        resolve(socket);
      };
      socket.onerror = () => {
        timers.clearTimeout(timeout);
        reject(new Error('STT WS open failed'));
      };
    });

    connecting.finally(() => {
      if (gen === generation) connecting = null;
    });

    socket.onmessage = (e) => {
      if (gen !== generation || ws !== socket) return;
      if (e.data instanceof ArrayBuffer) return; // 不应收到二进制帧
      let ev;
      try { ev = JSON.parse(e.data); } catch (_) { return; }
      if (ev.type === 'ready') {
        if (onReady) onReady(ev);
      } else if (ev.type === 'stt_partial') {
        if (onPartial) onPartial(ev.text || '');
      } else if (ev.type === 'stt_final') {
        if (onFinal) onFinal(ev.text || '');
      } else if (ev.type === 'stt_error') {
        if (onError) onError(ev.detail || 'unknown error');
      }
    };

    socket.onclose = () => {
      if (gen !== generation || ws !== socket) return;
      ws = null;
    };

    return connecting;
  }

  /**
   * 发送一帧 PCM 音频（Float32Array @设备采样率）。
   * 转为 16kHz mono s16le 后通过 WebSocket binary 帧发送。
   */
  function sendAudio(float32, deviceRate) {
    if (!ws || ws.readyState !== 1) return;
    // 重采样到 16kHz
    const targetRate = 16000;
    let resampled;
    if (deviceRate === targetRate) {
      resampled = float32;
    } else {
      const ratio = deviceRate / targetRate;
      const outLen = Math.ceil(float32.length / ratio);
      resampled = new Float32Array(outLen);
      for (let i = 0; i < outLen; i++) {
        const srcIdx = i * ratio;
        const lo = Math.floor(srcIdx);
        const hi = Math.min(lo + 1, float32.length - 1);
        const frac = srcIdx - lo;
        resampled[i] = float32[lo] * (1 - frac) + float32[hi] * frac;
      }
    }
    // Float32 → s16le
    const s16 = new Int16Array(resampled.length);
    for (let i = 0; i < resampled.length; i++) {
      const v = Math.max(-1, Math.min(1, resampled[i]));
      s16[i] = v < 0 ? v * 0x8000 : v * 0x7FFF;
    }
    ws.send(s16.buffer);
  }

  /**
   * 发送 end 信号：用户说完，服务端将已累积的音频做最终转写。
   */
  function end() {
    if (ws && ws.readyState === 1) {
      ws.send(JSON.stringify({ type: 'end' }));
    }
  }

  /**
   * 发送 abort 信号并关闭连接。
   */
  function abort() {
    generation++;
    if (ws) {
      try { ws.send(JSON.stringify({ type: 'abort' })); } catch (_) { /* 已关 */ }
      try { ws.close(); } catch (_) { /* 已关 */ }
      ws = null;
    }
    connecting = null;
  }

  function isConnected() {
    return ws && ws.readyState === 1;
  }

  return { connect, sendAudio, end, abort, isConnected };
}
