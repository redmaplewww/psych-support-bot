// PCM 播放排程特征测试：假 AudioContext 钉住重采样长度、首块起播时刻、
// 顺序排程无空隙、语速缩放、stopAll 复位、drained 60s 封顶。
// 行为基准取自 2026-09 前的线上实现（原 index.html 内联，现 static/js/voice/pcm.js）。
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createPcm } from '../../src/psych_support_bot/static/js/voice/pcm.js';

function fakeCtx(sampleRate) {
  return {
    sampleRate,
    currentTime: 100.0,
    state: 'running',
    resume: async () => {},
    destination: {},
    createBuffer(channels, len, rate) {
      return { length: len, sampleRate: rate, duration: len / rate, getChannelData: () => new Float32Array(len) };
    },
    createBufferSource() {
      return {
        buffer: null,
        playbackRate: { value: 1 },
        connected: false,
        startedAt: null,
        stopped: false,
        connect() { this.connected = true; },
        start(t) { this.startedAt = t; },
        stop() { this.stopped = true; },
      };
    },
  };
}

function makePcm({ rate = 24000, ctxRate = 48000, speed = 1.0, ctx = null } = {}) {
  const logs = { debug: [] };
  const fake = ctx ?? fakeCtx(ctxRate);
  const api = createPcm({
    createContext: () => fake,
    getSpeed: () => speed,
    debug: (m) => logs.debug.push(m),
  });
  api.state.rate = rate;
  return { api, fake, logs };
}

// 生成 1 秒的 s16le mono 音频字节
const oneSecondBytes = (rate) => new ArrayBuffer(rate * 2);

test('feed：首块在 currentTime+0.02 起播，后续块无空隙顺排', () => {
  const { api, fake } = makePcm();
  const b1 = oneSecondBytes(24000);
  const start1 = api.feed(b1);
  assert.ok(Math.abs(start1 - (fake.currentTime + 0.02)) < 1e-9);
  const start2 = api.feed(b1);
  assert.ok(Math.abs(start2 - (start1 + 1.0)) < 1e-9); // 1s 块播完接下一块
  const start3 = api.feed(b1);
  assert.ok(Math.abs(start3 - (start1 + 2.0)) < 1e-9);
});

test('feed：24k→48k 线性重采样长度翻倍', () => {
  const { api, fake } = makePcm();
  let madeLen = 0;
  const origCreate = fake.createBuffer.bind(fake);
  fake.createBuffer = (ch, len, rate) => { madeLen = len; return origCreate(ch, len, rate); };
  api.feed(new ArrayBuffer(24000 * 2)); // 24000 样本 @24k
  assert.equal(madeLen, 48000); // 24k→48k：样本数 ×2
});

test('feed：语速 1.4 缩放排程时长（变速度也变排程）', () => {
  const { api, fake } = makePcm({ speed: 1.4 });
  const start1 = api.feed(oneSecondBytes(24000));
  const start2 = api.feed(oneSecondBytes(24000));
  assert.ok(Math.abs((start2 - start1) - 1 / 1.4) < 1e-9);
});

test('stopAll：停掉在播源并把 cursor 复位到当前时刻', () => {
  const { api, fake } = makePcm();
  api.feed(oneSecondBytes(24000));
  api.feed(oneSecondBytes(24000));
  fake.currentTime = 105.0;
  api.stopAll();
  assert.ok(Math.abs(api.state.cursor - 105.0) < 1e-9);
  assert.equal(api.state.sources.length, 0);
});

test('drained：ctx 未建立即 resolve；队列剩 2s 则等 ~2s（60s 封顶）', async () => {
  const { api, fake } = makePcm();
  await api.drained(); // ctx=null 分支
  api.feed(oneSecondBytes(24000));
  api.feed(oneSecondBytes(24000)); // cursor ≈ 102.02，剩 ~2s
  const t0 = Date.now();
  await api.drained();
  const waited = Date.now() - t0;
  assert.ok(waited >= 1900 && waited < 3000, `waited=${waited}`);
});

test('suspended ctx：ensureCtx 调 resume 并只告警一次', async () => {
  const { api, fake, logs } = makePcm();
  fake.state = 'suspended';
  api.ensureCtx();
  api.ensureCtx();
  assert.equal(logs.debug.filter((m) => m.includes('suspended')).length, 1);
  assert.equal(api.state.warnedSuspended, true);
});
