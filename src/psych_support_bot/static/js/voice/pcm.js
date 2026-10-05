// PCM 流式播放：二进制帧到达即入 WebAudio 队列，首块即播（不等整句合成完）。
// 服务端 tts_live 以 PCM(s16le mono，采样率由 ready 事件声明) 二进制帧直推音频；
// ready 未声明 pcm（旧服务端/降级）时上层自动回到 mp3-b64 整句合成再播的旧路径。
// 从 index.html 原样迁出（行为不变）；AudioContext 创建/语速读取/调试打点注入，
// node --test 用假 AudioContext 钉住重采样与排程行为。
export function createPcm({ createContext, getSpeed, debug }) {
  const PCM = {
    ctx: null, cursor: 0, rate: 32000, sources: [], warnedSuspended: false,
    // 语速旋钮（playbackRate 同步缩放播放与排程）：localStorage np_tts_speed
    // 可调 0.8-1.4，默认 1.0（MiMo 冰糖原生语速）。改完刷新页面生效。
    speed: 1.0,
  };
  PCM.speed = getSpeed();

  function ensureCtx() {
    try {
      if (!PCM.ctx) {
        const ctx = createContext();
        if (!ctx) return null;
        PCM.ctx = ctx;
        PCM.cursor = PCM.ctx.currentTime;
      }
      if (PCM.ctx.state === 'suspended') {
        PCM.ctx.resume().catch(() => {});
        if (!PCM.warnedSuspended) {
          PCM.warnedSuspended = true;
          debug('audio ctx suspended → resume()（持续挂起=无声）');
        }
      }
      return PCM.ctx;
    } catch (err) { return null; }
  }

  // buf: ArrayBuffer(s16le mono @ PCM.rate)；返回本块排定的起播时刻（秒，
  // 供字幕对齐），失败返回 null。32k→设备采样率线性重采样（单声道 64KB/s
  // 量级，代价可忽略；ctx.sampleRate 常为 48k，ratio=2/3 也稳）
  function feed(buf) {
    const ctx = ensureCtx();
    if (!ctx) return null;
    const i16 = new Int16Array(buf);
    if (!i16.length) return null;
    const ratio = PCM.rate / ctx.sampleRate;
    const len = Math.max(1, Math.round(i16.length / ratio));
    const out = ctx.createBuffer(1, len, ctx.sampleRate);
    const ch = out.getChannelData(0);
    for (let i = 0; i < len; i++) {
      const s = i * ratio, i0 = s | 0, i1 = Math.min(i16.length - 1, i0 + 1), f = s - i0;
      ch[i] = (i16[i0] * (1 - f) + i16[i1] * f) / 32768;
    }
    const src = ctx.createBufferSource();
    src.buffer = out;
    src.playbackRate.value = PCM.speed; // 语速旋钮：变速度也变排程时长
    src.connect(ctx.destination);
    const start = Math.max(ctx.currentTime + 0.02, PCM.cursor);
    src.start(start);
    PCM.cursor = start + out.duration / PCM.speed;
    PCM.sources.push(src);
    src.onended = () => { PCM.sources = PCM.sources.filter((x) => x !== src); };
    return start;
  }

  function stopAll() {
    PCM.sources.forEach((s) => { try { s.stop(); } catch (err) { /* 已自然结束 */ } });
    PCM.sources = [];
    if (PCM.ctx) PCM.cursor = PCM.ctx.currentTime;
  }

  // 等本地队列播完（round_end 收束：音频放完才撤黄框）；60s 封顶防悬挂
  function drained() {
    if (!PCM.ctx) return Promise.resolve();
    const wait = Math.max(0, Math.min(60000, (PCM.cursor - PCM.ctx.currentTime) * 1000));
    return new Promise((r) => setTimeout(r, wait + 80));
  }

  return { state: PCM, ensureCtx, feed, stopAll, drained };
}
