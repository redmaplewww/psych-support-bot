// TTS live 状态机特征测试：假 WebSocket/PCM/定时器把线上行为原样钉住——
// 连接代次守卫、pendingEnd 握手期补发、error 转投 HTTP 队列、25s 收束硬上限、
// 无首音 6s 加速还麦、字幕铺字时长公式。这些行为全部来自实证修过的 bug（见各用例注释）。
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createTtsLive, ROUND_TIMEOUT_MS } from '../../src/psych_support_bot/static/js/voice/live.js';
import * as proto from '../../src/psych_support_bot/static/js/voice/protocol.js';

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

class FakeWS {
  constructor(url) {
    this.url = url;
    this.readyState = 0;
    this.sent = [];
    this.onopen = this.onclose = this.onmessage = this.onerror = null;
    FakeWS.created.push(this);
    queueMicrotask(() => { this.readyState = 1; if (this.onopen) this.onopen(); });
  }
  send(d) { this.sent.push(d); }
  close() { this.readyState = 3; if (this.onclose) this.onclose(); }
  // 测试辅助：模拟服务端控制面/音频帧
  serverJson(obj) { if (this.onmessage) this.onmessage({ data: JSON.stringify(obj) }); }
  serverBinary(bytes) { if (this.onmessage) this.onmessage({ data: new ArrayBuffer(bytes) }); }
}
FakeWS.created = [];

function makeDeps(over = {}) {
  const calls = { appendRound: [], turnMark: [], fallback: [], debug: [], stopAll: 0, drained: 0, resetShown: 0, playBlob: [] };
  let feedCursor = 10.0;
  const pcmState = { ctx: { currentTime: 10.0, state: 'running' }, cursor: 10.0, rate: 24000, speed: 1.0, sources: [] };
  const deps = {
    wsUrl: () => 'ws://test/v1/voice/tts/live',
    WebSocket: FakeWS,
    debug: (m) => calls.debug.push(m),
    pcm: {
      state: pcmState,
      feed: () => { feedCursor += 0.008; return feedCursor; }, // 起播时刻贴 currentTime（leadMs<20 → 字幕即时铺）
      stopAll: () => { calls.stopAll += 1; },
      drained: () => { calls.drained += 1; return Promise.resolve(); },
    },
    playBlob: async (blob, chars, text) => { calls.playBlob.push({ size: blob.size, chars, text }); },
    ui: {
      getRound: () => 7,
      appendRound: (round, text, elapsedMs, totalMs) => calls.appendRound.push({ round, text, elapsedMs, totalMs }),
      turnMark: (s) => calls.turnMark.push(s),
      turnTryReport: () => {},
      resetShownChars: () => { calls.resetShown += 1; },
    },
    ...over,
  };
  return { deps, calls, pcmState };
}

async function openPcmSession(live, { sampleRate = 24000 } = {}) {
  const ws = await live.ttsLiveEnsure();
  ws.serverJson({ type: 'ready', audio: { format: 'pcm', sample_rate: sampleRate } });
  return ws;
}

test('常量：收束硬上限 25s（与服务端 30s recv 对齐的契约）', () => {
  assert.equal(ROUND_TIMEOUT_MS, 25000);
});

test('流式轮：say → ready(pcm) → 二进制帧 → sentence_end 驱动字幕铺字', async () => {
  const { deps, calls } = makeDeps();
  const live = createTtsLive(deps);
  live.ttsLiveSay('你好呀。');
  const ws = await openPcmSession(live);
  ws.serverBinary(48000); // 24000 样本 @24k = 1.0s
  ws.serverJson({ type: 'sentence_end' });
  await sleep(20); // firstAudioMarked 的 turnMark 走 10ms 定时器
  assert.equal(live.state.pcm, true);
  assert.ok(calls.appendRound.length === 1, '字幕未随句上屏');
  const r = calls.appendRound[0];
  assert.equal(r.round, 7);
  assert.equal(r.totalMs, 1000); // samples/rate*1000，未触 700/9000 夹逼
  assert.ok(r.elapsedMs <= 0, '起播时刻未过 → 铺字不等待（leadMs≤20 即时）');
  assert.deepEqual(calls.turnMark, ['firstAudio']);
});

test('字幕时长夹逼：极短句保底 700ms、超长句封顶 9000ms', async () => {
  const { deps, calls } = makeDeps();
  const live = createTtsLive(deps);
  const ws = await openPcmSession(live);
  live.ttsLiveSay('短句。');
  live.ttsLiveSay('这是一句特别特别长长长长长长长长长长长长长长长长长长长长长长长长的话。');
  ws.serverBinary(480); // 240 样本 → 10ms → 夹到 700
  ws.serverJson({ type: 'sentence_end' });
  ws.serverBinary(480000 * 2); // 480000 样本 → 20s → 夹到 9000
  ws.serverJson({ type: 'sentence_end' });
  assert.equal(calls.appendRound.length, 2);
  assert.equal(calls.appendRound[0].totalMs, 700);
  assert.equal(calls.appendRound[1].totalMs, 9000);
});

test('ready 未声明 pcm → 旧路径：b64 攒句 + sentence_end 冲刷 playBlob', async () => {
  const { deps, calls } = makeDeps();
  const live = createTtsLive(deps);
  live.ttsLiveSay('旧路径的一句。');
  const ws = await live.ttsLiveEnsure();
  ws.serverJson({ type: 'ready', audio: { format: 'mp3', sample_rate: 44100 } });
  ws.serverJson({ type: 'audio', b64: Buffer.from('ID3data').toString('hex') });
  ws.serverJson({ type: 'sentence_end' });
  await Promise.resolve(); // chain 微任务
  assert.equal(live.state.pcm, false);
  assert.equal(calls.playBlob.length, 1);
  assert.equal(calls.playBlob[0].text, '旧路径的一句。');
  assert.ok(calls.playBlob[0].size > 0);
});

test('error 降级：当轮剩余 say 静默丢弃（文字由 final 上屏），通道弃用待下轮重试', async () => {
  const { deps, calls } = makeDeps();
  const live = createTtsLive(deps);
  const ws = await openPcmSession(live);
  live.ttsLiveSay('第一句。');
  live.ttsLiveSay('第二句。');
  ws.serverJson({ type: 'error', detail: 'upstream 429' });
  assert.equal(live.state.failed, true);
  assert.deepEqual(live.state.pending, [], '剩余 say 丢弃');
  assert.deepEqual(live.state.pendingTexts, [], '字幕队列丢弃（不转投 HTTP，二选一语义）');
  assert.equal(calls.debug.some((m) => m.includes('upstream 429')), true);
  // 下一轮开拍：beginTurn 重置降级标志 → 自动重试探路
  live.ttsLiveBeginTurn();
  assert.equal(live.state.failed, false);
});

test('连接代次守卫：废弃连接的在途帧一律作废（防串音根因）', async () => {
  const { deps, calls } = makeDeps();
  const live = createTtsLive(deps);
  const ws1 = await openPcmSession(live);
  live.ttsLiveReset(); // 新一代：ws 置空
  const ws2 = await openPcmSession(live);
  assert.notEqual(ws1, ws2);
  const samplesBefore = live.state.sentenceSamples;
  ws1.serverBinary(4800); // 旧连接残余帧
  assert.equal(live.state.sentenceSamples, samplesBefore, '旧帧被代次守卫丢弃');
  ws2.serverBinary(4800);
  assert.equal(live.state.sentenceSamples, 2400);
});

test('end 在握手期排队：绝不静默丢（丢=朗读读到一半没了，2026-09-10 实证）', async () => {
  const { deps, calls } = makeDeps({ roundTimeoutMs: 40 }); // 短超时：无 round_end 也快速收束
  const live = createTtsLive(deps);
  live.ttsLiveSay('第一句。'); // 触发 ensure（WS 尚未 open）
  await live.ttsLiveEndRound(); // ws 未就绪 → end 排队
  await sleep(10); // 微任务：ws open → drain pending → 补发 end
  const ws = FakeWS.created[FakeWS.created.length - 1];
  const types = ws.sent.map((s) => JSON.parse(s).type);
  assert.deepEqual(types, ['say', 'end']);
  assert.equal(live.state.pendingEnd, false);
});

test('收束硬上限：上游挂死时按 roundTimeoutMs 强制收束并掐队列', async () => {
  const { deps, calls } = makeDeps({ roundTimeoutMs: 30 });
  const live = createTtsLive(deps);
  const t0 = Date.now();
  await live.ttsLiveEndRound(); // 无 WS 无 round_end → 30ms 超时兜底
  const waited = Date.now() - t0;
  assert.ok(waited >= 25 && waited < 300, `waited=${waited}`);
  assert.ok(calls.debug.some((m) => m.includes('force finish')));
  assert.ok(calls.stopAll >= 1, '超时兜底必须掐掉滞留队列');
  assert.equal(live.state.ws, null);
});

// 假定时器：捕获收轮上限的排程值（不真等）
function makeFakeTimers() {
  const scheduled = [];
  return {
    scheduled,
    timers: {
      setTimeout: (fn, ms) => { scheduled.push({ fn, ms }); return scheduled.length; },
      clearTimeout: (id) => { if (scheduled[id - 1]) scheduled[id - 1].fn = null; },
    },
  };
}

test('无首音收束加速：上游挂死 6s 还麦，不扣满 25s（第二次说话录不进的根因链）', async () => {
  const { scheduled, timers } = makeFakeTimers();
  const { deps, calls } = makeDeps({ roundTimeoutMs: 25000, timers });
  const live = createTtsLive(deps);
  const ws = await openPcmSession(live);
  live.ttsLiveSay('挂在上游的一句话。');
  live.state.pendingTexts = ['死轮残留句']; // 模拟超时前已 say 未播的字幕
  const p = live.ttsLiveEndRound(); // 首音从未出现（firstAudioMarked=false）
  const t = scheduled.find((s) => s.fn && s.ms !== 5000); // 5s 是 WS open 守卫，非收轮上限
  assert.equal(t.ms, 6000, '无首音：6s 收束，麦克风不被黄框假死挂满 25s');
  t.fn(); // 超时触发
  await p;
  assert.ok(calls.debug.some((m) => m.includes('6000ms), force finish')));
  assert.equal(live.state.ws, null, '挂死的会话就地弃用，下一轮换新连接');
  assert.equal(ws.readyState, 3, '中毒 WS 已关闭');
  assert.deepEqual(live.state.pendingTexts, [], '死轮字幕队列清空，不污染下一轮（防错位读旧句）');
  assert.equal(live.state.finalTakeover, false);
});

test('ttsLiveBeginTurn：清残留字幕态，finalTakeover 不吞下一轮 sentence_end 字幕', () => {
  const { deps } = makeDeps();
  const live = createTtsLive(deps);
  // 模拟上一轮结束后的残留：定稿接管过 + 有未消费字幕 + 首音标记
  live.state.finalTakeover = true;
  live.state.pendingTexts = ['上一轮残留'];
  live.state.pending = ['未发送'];
  live.state.firstAudioMarked = true;
  live.state.sentenceSamples = 4800;
  live.ttsLiveBeginTurn();
  assert.equal(live.state.finalTakeover, false, '残留 finalTakeover 会静默吞掉下一轮全部字幕');
  assert.deepEqual(live.state.pendingTexts, []);
  assert.deepEqual(live.state.pending, []);
  assert.equal(live.state.firstAudioMarked, false);
  assert.equal(live.state.sentenceSamples, 0);
  // 通道语义不动：ws/failed 由各自失败路径管理
  assert.equal(live.state.failed, false);
});

test('有首音收束：已在播放则保持 roundTimeoutMs 等尾句播完', async () => {
  const { scheduled, timers } = makeFakeTimers();
  const { deps } = makeDeps({ roundTimeoutMs: 25000, timers });
  const live = createTtsLive(deps);
  live.state.firstAudioMarked = true; // 首音已出：真在播，回声防护必须维持
  const p = live.ttsLiveEndRound();
  const t = scheduled.find((s) => s.fn && s.ms !== 5000); // 排除 WS open 守卫
  assert.equal(t.ms, 25000, '首音已出：保持 25s 等尾句');
  t.fn();
  await p;
});

test('ready.tts 画像：first_audio_timeout_ms 下发后，无首音收束按下发值放宽', async () => {
  const { scheduled, timers } = makeFakeTimers();
  const { deps, calls } = makeDeps({ roundTimeoutMs: 25000, timers });
  const live = createTtsLive(deps);
  const ws = await live.ttsLiveEnsure();
  // 音色复刻场景：服务端经 ready 下发首包预算（兼容模式流式，首包 5-20s）
  ws.serverJson({ type: 'ready', audio: { format: 'pcm', sample_rate: 24000 }, tts: { first_audio_timeout_ms: 20000 } });
  live.ttsLiveSay('复刻音色的一句。');
  const p = live.ttsLiveEndRound();
  const t = scheduled.find((s) => s.fn && s.ms !== 5000); // 排除 WS open 守卫
  assert.equal(t.ms, 20000, '复刻音色：按下发值放宽，6s 会掐掉整轮（20260912 只读到最短一句）');
  t.fn();
  await p;
  assert.ok(calls.debug.some((m) => m.includes('20000ms), force finish')));
});

test('ready.tts 画像越界：夹逼到 [6000, 25000]', async () => {
  const { scheduled, timers } = makeFakeTimers();
  const { deps } = makeDeps({ roundTimeoutMs: 25000, timers });
  const live = createTtsLive(deps);
  const ws = await live.ttsLiveEnsure();
  ws.serverJson({ type: 'ready', audio: { format: 'pcm', sample_rate: 24000 }, tts: { first_audio_timeout_ms: 300 } });
  live.ttsLiveSay('一句。');
  const p = live.ttsLiveEndRound();
  const t = scheduled.find((s) => s.fn && s.ms !== 5000);
  assert.equal(t.ms, 6000, '下发值过小按 6000 下限夹逼（保留防挂死快收束语义）');
  t.fn();
  await p;
});

test('round_end 收束：等 PCM 队列播完（drained）才唤醒等待者', async () => {
  const { deps, calls } = makeDeps();
  const live = createTtsLive(deps);
  const ws = await openPcmSession(live);
  const p = live.ttsLiveEndRound();
  ws.serverJson({ type: 'round_end' });
  await p;
  assert.equal(calls.drained, 1);
  assert.equal(live.state.roundDone, null);
});

test('killLiveWs：断连弃用，残余帧不再进入处理链', async () => {
  const { deps, calls } = makeDeps();
  const live = createTtsLive(deps);
  const ws = await openPcmSession(live);
  live.killLiveWs(ws);
  assert.equal(live.state.ws, null);
  const samplesBefore = live.state.sentenceSamples;
  ws.serverBinary(4800); // handler 已被摘除 → 无效果（FakeWS.serverBinary 检查 onmessage）
  assert.equal(live.state.sentenceSamples, samplesBefore);
});

test('ttsLiveRound 整轮：say 全文+end、重置字幕预算、round_end 后 resolve', async () => {
  const { deps, calls } = makeDeps();
  const live = createTtsLive(deps);
  const p = live.ttsLiveRound('整轮全文。');
  await sleep(5);
  const ws = FakeWS.created[FakeWS.created.length - 1];
  assert.deepEqual(ws.sent.map((s) => JSON.parse(s).type), ['say', 'end']);
  assert.equal(calls.resetShown, 1); // revise 整轮重读：live 泡字数预算清零
  ws.serverJson({ type: 'ready', audio: { format: 'pcm', sample_rate: 24000 } });
  ws.serverJson({ type: 'round_end' });
  await p;
  assert.equal(calls.stopAll >= 1, true);
});
