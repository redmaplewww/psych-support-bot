// TTS live 全双工会话状态机（/v1/voice/tts/live）：LLM 流式句直灌（say），
// 服务端 PCM 帧直推 + sentence_end/round_end 控制面。对话朗读的唯一通道
// （二选一决策，见 docs/technical/VOICE_DECISIONS.md D6）：失败=当轮无声、
// 每轮开拍重试探路，不做 HTTP 逐句回退。协议权威定义见
// src/psych_support_bot/infra/voice/protocol.py（前端镜像 js/voice/protocol.js）。
// 从 index.html 原样迁出；WS 地址/PCM 播放/字幕回调/定时器全部注入，
// node --test 用假 WebSocket 钉住状态机行为。

// round 收束硬上限——MiMo 免费期上游偶发挂死会让服务器 round_end 迟到两
// 分钟，黄框假死期间麦克风一直被挂起（第二次说话录不进的根因链）。取值
// 与服务端 _TTS_LIVE_UPSTREAM_RECV_TIMEOUT=30s 对齐（略小，前端先收束）；
// 首音未出（上游挂死、无回声可防）时 6s 加速还麦并弃用中毒 WS，见
// ttsLiveEndRound 与 docs/technical/VOICE_PROTOCOL.md 超时对齐表。
export const ROUND_TIMEOUT_MS = 25000;

export function createTtsLive(deps) {
  const { wsUrl, debug, pcm, playBlob, ui } = deps;
  const timers = deps.timers || {
    setTimeout: (...a) => setTimeout(...a),
    clearTimeout: (...a) => clearTimeout(...a),
  };
  // 注入 WS 构造器：浏览器用原生 WebSocket；node 环境恰好也有全局 WebSocket
  // （undici，会真联网），测试必须显式给假的。
  const WebSocketCtor = deps.WebSocket || globalThis.WebSocket;
  const roundTimeoutMs = deps.roundTimeoutMs || ROUND_TIMEOUT_MS;

  let generation = 0;
  let connecting = null;
  let rejectOpening = null;
  let endPromise = null;
  let endResolve = null;
  let watchdog = null;
  let receivedEnd = false;
  let nextSentence = 0;
  let activeSentence = null;
  const utterances = new Map();

  const TTS_LIVE = {
    _roundGen: generation,
    ws: null, failed: false, pending: [], roundStarted: false, ended: false,
    curBytes: [], curChars: 0,
    pendingTexts: [], // 已 say 未播的句子文本，随 sentence_end 出队随音频起播上屏
    pcm: false, sentenceStart: null, firstAudioMarked: false, sentenceSamples: 0, pendingEnd: false, finalTakeover: false, // PCM 流式播放态（②）+ 每句样本数（字幕按时长铺）+ 定稿接管标记
    chain: Promise.resolve(),
    roundDone: null,
    firstAudioTimeoutMs: 0, // ready.tts 下发的无首音收束预算（0=未下发，用 6s 缺省）
  };

  function ttsLiveReset() { ttsLiveBeginTurn(); }

  function ttsLiveBeginTurn() {
    generation++;
    if (watchdog != null) timers.clearTimeout(watchdog);
    watchdog = null;
    killLiveWs(TTS_LIVE.ws);
    TTS_LIVE.ws = null; // 防御性：确保旧连接指针不残留
    if (rejectOpening) rejectOpening(new Error('round cancelled'));
    rejectOpening = null; connecting = null;
    if (endResolve) endResolve();
    endResolve = null; endPromise = null; receivedEnd = false;
    nextSentence = 0; activeSentence = null; utterances.clear();
    Object.assign(TTS_LIVE, {
      failed: false, pending: [], pendingTexts: [], roundStarted: false, ended: false,
      curBytes: [], curChars: 0, pcm: false, sentenceStart: null,
      firstAudioMarked: false, sentenceSamples: 0, pendingEnd: false,
      finalTakeover: false, chain: Promise.resolve(), roundDone: null, firstAudioTimeoutMs: 0,
      _roundGen: generation, // finishRound 守卫：新轮开始后旧轮的异步回调不再修改状态
    });
    pcm.stopAll();
  }

  function finishRound(failed = false) {
    // 新轮已开始（ttsLiveBeginTurn 重置了 _roundGen）：旧轮的异步回调
    // （watchdog 超时、ttsLiveEnsure 失败）不得修改新轮状态。
    if (TTS_LIVE._roundGen !== generation) return;
    if (failed) TTS_LIVE.failed = true;
    if (watchdog != null) timers.clearTimeout(watchdog);
    watchdog = null;
    TTS_LIVE.roundDone = null;
    if (endResolve) endResolve();
    endResolve = null;
  }

  // Watch upstream inactivity, not total speech duration. Audio already queued
  // must drain even when it is longer than the network timeout.
  function armWatchdog() {
    if (!endPromise || receivedEnd) return;
    if (watchdog != null) timers.clearTimeout(watchdog);
    const cap = TTS_LIVE.firstAudioMarked ? roundTimeoutMs
      : Math.min(roundTimeoutMs, TTS_LIVE.firstAudioTimeoutMs || 6000);
    const gen = generation;
    watchdog = timers.setTimeout(() => {
      if (gen !== generation) return;
      debug('tts live: round timeout(' + cap + 'ms), force finish');
      killLiveWs(TTS_LIVE.ws);
      TTS_LIVE.pending = []; TTS_LIVE.pendingTexts = [];
      TTS_LIVE.curBytes = []; TTS_LIVE.curChars = 0;
      TTS_LIVE.pendingEnd = false; TTS_LIVE.finalTakeover = false;
      pcm.stopAll();
      finishRound(true);
    }, cap);
  }

  function ttsHexToBytes(hex) {
    const out = new Uint8Array(hex.length / 2);
    for (let i = 0; i < out.length; i++) out[i] = parseInt(hex.substr(i * 2, 2), 16);
    return out;
  }

  // 当前句音频攒齐（sentence_end）→ 入串行播放链（句间零空等）。
  // 文本随播上屏：MiniMax 的分句边界与我们的 say 近似 1:1（LLM 句已短），
  // 出队错位最坏是某句文字提前/延后一拍，final 会统一纠正。
  function ttsLiveFlushSentence() {
    if (!TTS_LIVE.curBytes.length) return;
    const bytes = TTS_LIVE.curBytes;
    const chars = TTS_LIVE.curChars;
    const text = TTS_LIVE.pendingTexts.shift() || '';
    TTS_LIVE.curBytes = []; TTS_LIVE.curChars = 0;
    const blob = new Blob(bytes, { type: 'audio/mpeg' });
    TTS_LIVE.chain = TTS_LIVE.chain.then(() => playBlob(blob, chars, text));
  }

  async function ttsLiveEnsure() {
    // 复用已有连接：OPEN 直接返回；CONNECTING 时返回正在握手的同一个 promise，
    // 避免投机回复集中发句时为每句各建一条 WS。
    if (TTS_LIVE.ws) {
      if (TTS_LIVE.ws.readyState === 1) return TTS_LIVE.ws;
      if (TTS_LIVE.ws.readyState === 0 && connecting) return connecting;
    }
    if (connecting) return connecting;
    const gen = generation;
    const ws = new WebSocketCtor(wsUrl());
    TTS_LIVE.ws = ws;
    ws.binaryType = 'arraybuffer';
    ws.onmessage = (e) => {
      // 连接代次守卫：abort 后 MiniMax 取消生效前仍会泵出残余 PCM 块，
      // 旧连接在途帧若入队就是"多条信息杂乱堆叠"的串音根因——一律作废
      if (gen !== generation || TTS_LIVE.ws !== ws) return;
      // 二进制帧 = PCM 音频块（②协议）：直接入队，首块即排定起播
      if (TTS_LIVE.pcm && e.data instanceof ArrayBuffer) {
        TTS_LIVE.sentenceSamples += e.data.byteLength / 2; // s16le 单声道 → 样本数
        const start = pcm.feed(e.data);
        if (start != null) {
          if (TTS_LIVE.sentenceStart == null) {
            TTS_LIVE.sentenceStart = start;
            const text = activeSentence?.text || TTS_LIVE.pendingTexts[0] || '';
            if (text && ui.playSentence) ui.playSentence({
              text, start, round: ui.getRound(), sentenceId: activeSentence?.sentence_id,
              clock: () => pcm.state.ctx?.state === 'running' ? pcm.state.ctx.currentTime : -Infinity,
            });
          } // 当前句的首块时刻
          if (!TTS_LIVE.firstAudioMarked) {
            TTS_LIVE.firstAudioMarked = true;
            timers.setTimeout(() => { ui.turnMark('firstAudio'); ui.turnTryReport(); },
              Math.max(0, (start - pcm.state.ctx.currentTime) * 1000));
          }
        }
        armWatchdog();
        return;
      }
      let ev; try { ev = JSON.parse(e.data); } catch (err) { return; }
      if (ev.type === 'ready') {
        // 能力协商：服务端声明 PCM → 流式起播；否则维持 mp3-b64 整句旧路径
        TTS_LIVE.pcm = !!(ev.audio && ev.audio.format === 'pcm');
        if (ev.audio && ev.audio.sample_rate) pcm.state.rate = ev.audio.sample_rate;
        // TTS 延迟画像（音色复刻=兼容模式流式，首包 5-20s）：服务端下发的
        // 无首音收束预算；预置音色不带此字段，维持 6s 快收束还麦
        if (ev.tts && typeof ev.tts.first_audio_timeout_ms === 'number') {
          TTS_LIVE.firstAudioTimeoutMs = Math.max(6000, Math.min(25000, ev.tts.first_audio_timeout_ms));
        }
        armWatchdog();
      } else if (ev.type === 'sentence_start') {
        if (ev.round_id && ev.round_id !== String(generation)) return;
        activeSentence = utterances.get(ev.sentence_id) || null;
      } else if (ev.type === 'audio' && ev.b64) {
        TTS_LIVE.curBytes.push(ttsHexToBytes(ev.b64));
        TTS_LIVE.curChars += 4; // 粗略累加字数供播放看护估算
      } else if (ev.type === 'sentence_end') {
        if (ev.round_id && ev.round_id !== String(generation)) return;
        if (activeSentence && ev.sentence_id && activeSentence.sentence_id !== ev.sentence_id) return;
        if (ui.playSentence) {
          TTS_LIVE.pendingTexts.shift();
          utterances.delete(ev.sentence_id); activeSentence = null;
          TTS_LIVE.sentenceStart = null; TTS_LIVE.sentenceSamples = 0;
          armWatchdog();
          return;
        }
        if (TTS_LIVE.pcm) {
          // 定稿接管后正式气泡负责铺字，live 字幕退场（只出队、清计数）
          if (TTS_LIVE.finalTakeover) {
            TTS_LIVE.pendingTexts.shift();
            TTS_LIVE.sentenceStart = null; TTS_LIVE.sentenceSamples = 0;
            return;
          }
          // 文字贴语音铺：reveal 总时长 = 该句 PCM 实际时长；该句音频起播
          // 时刻若已过（合成快于实时、sentence_end 晚于起播），文字按已过
          // 时间追平进度。leadMs>0 则等音频开场再开始铺。
          const text = TTS_LIVE.pendingTexts.shift() || '';
          const at = TTS_LIVE.sentenceStart;
          const samples = TTS_LIVE.sentenceSamples;
          TTS_LIVE.sentenceStart = null; TTS_LIVE.sentenceSamples = 0;
          if (text) {
            const durMs = Math.max(700, Math.min(9000, (samples / pcm.state.rate) * 1000 / pcm.state.speed));
            const leadMs = (at != null && pcm.state.ctx) ? (at - pcm.state.ctx.currentTime) * 1000 : 0;
            const round = ui.getRound();
            if (leadMs > 20) {
              timers.setTimeout(() => ui.appendRound(round, text, 0, durMs), leadMs);
            } else {
              ui.appendRound(round, text, -leadMs, durMs);
            }
          }
        } else {
          ttsLiveFlushSentence();
        }
      } else if (ev.type === 'round_end') {
        receivedEnd = true;
        if (watchdog != null) timers.clearTimeout(watchdog);
        watchdog = null;
        if (!TTS_LIVE.pcm) ttsLiveFlushSentence();
        const completion = TTS_LIVE.pcm ? pcm.drained() : TTS_LIVE.chain;
        TTS_LIVE.chain = completion;
        completion.then(() => { if (gen === generation) finishRound(); });
      } else if (ev.type === 'error') {
        debug('tts live: ' + (ev.detail || 'error'));
        // 上游会话中断（配额/风控等）：本轮剩余 say 不再朗读（文字照常由
        // final 上屏），WS 通道弃用；下一轮 ttsLiveBeginTurn 重置降级标志
        // 自动重试探路。不做运行时 HTTP 逐句回退——单句失败静默跳过是不可
        // 观测的劣化（20260912 实证：只读到最后一句）。
        TTS_LIVE.failed = true;
        TTS_LIVE.pending = [];
        TTS_LIVE.pendingTexts = [];
        pcm.stopAll();
        finishRound(true);
        killLiveWs(ws);
      }
    };
    ws.onclose = () => {
      if (gen !== generation || TTS_LIVE.ws !== ws) return;
      if (rejectOpening) rejectOpening(new Error('ws closed before opening'));
      TTS_LIVE.ws = null;
      if (!receivedEnd) {
        debug('tts live: closed before round_end');
        pcm.stopAll(); finishRound(true);
      }
    };
    const opening = new Promise((resolve, reject) => {
      const timer = timers.setTimeout(() => reject(new Error('ws open timeout')), 5000);
      const settle = (fn, value) => { timers.clearTimeout(timer); rejectOpening = null; fn(value); };
      rejectOpening = (err) => settle(reject, err);
      ws.onopen = () => settle(resolve, ws);
      ws.onerror = () => settle(reject, new Error('ws open failed'));
    });
    connecting = opening;
    try {
      await opening;
      if (gen !== generation) throw new Error('round cancelled');
      return ws;
    } catch (err) {
      if (gen === generation) { killLiveWs(ws); pcm.stopAll(); finishRound(true); }
      throw err;
    } finally {
      if (gen === generation) connecting = null;
    }
  }

  async function ttsLiveRound(text) {
    ttsLiveBeginTurn();
    ui.resetShownChars();
    // Full replies and revisions use exactly the same sentence queue.
    for (const sentence of (text.match(/[^。！？!?\n]+[。！？!?\n]*/g) || [text])) {
      ttsLiveSay(sentence);
    }
    await ttsLiveEndRound();
  }

  function ttsLiveSend(obj) {
    if (TTS_LIVE.ws && TTS_LIVE.ws.readyState === 1) TTS_LIVE.ws.send(JSON.stringify(obj));
  }

  // 打断 = 断连弃用：abort 帧发出后立刻掐掉这条 WS——MiniMax 的 task_cancel
  // 生效前残余音频仍会下发，留着连接就是下一轮开头串进上一轮尾巴的杂音。
  function killLiveWs(ws) {
    if (!ws) return;
    try { ws.onmessage = ws.onclose = ws.onerror = null; ws.close(); } catch (err) { /* 已关 */ }
    if (TTS_LIVE.ws === ws) TTS_LIVE.ws = null;
  }

  // LLM 流式句：直灌同一 WS 会话（say），播放由 sentence_end 驱动；
  // ws 未就绪时排队 pending，就绪后 drain。不重置 curBytes——上一句的
  // 尾部音频可能仍在途，冲刷只由服务端 sentence_end 事件触发。
  function ttsLiveSay(text) {
    if (!text || TTS_LIVE.failed || TTS_LIVE.ended) return;
    const item = { type: 'say', text, round_id: String(generation), sentence_id: ++nextSentence };
    utterances.set(item.sentence_id, item);
    TTS_LIVE.pending.push(item);
    TTS_LIVE.pendingTexts.push(text);
    TTS_LIVE.roundStarted = true;
    if (TTS_LIVE.ws && TTS_LIVE.ws.readyState === 1) {
      ttsLiveDrainPending();
    } else if (!TTS_LIVE.failed) {
      const gen = generation;
      ttsLiveEnsure().then(() => { if (gen === generation) ttsLiveDrainPending(); }).catch(() => {});
    }
  }

  function ttsLiveDrainPending() {
    while (TTS_LIVE.pending.length && TTS_LIVE.ws && TTS_LIVE.ws.readyState === 1) {
      ttsLiveSend(TTS_LIVE.pending.shift());
    }
    // says 补发完后若有等待中的 end（握手期排队的），此刻一并送达
    if (TTS_LIVE.pendingEnd && TTS_LIVE.ws && TTS_LIVE.ws.readyState === 1) {
      TTS_LIVE.pendingEnd = false;
      ttsLiveSend({ type: 'end' });
    }
  }

  // 流式轮收轮：end + 等 round_end（此时 chain 里可能还有未播完的句，
  // 等 chain 也结束再 resolve，保证朗读完整）
  function ttsLiveEndRound() {
    if (endPromise) return endPromise;
    if (TTS_LIVE.failed) return Promise.resolve();
    TTS_LIVE.ended = true;
    endPromise = new Promise(resolve => { endResolve = resolve; });
    TTS_LIVE.roundDone = () => TTS_LIVE.chain.then(() => finishRound());
    if (receivedEnd) TTS_LIVE.chain.then(() => finishRound());
    else {
      TTS_LIVE.pendingEnd = true;
      ttsLiveDrainPending();
      armWatchdog();
    }
    return endPromise;
  }

  return {
    state: TTS_LIVE, ttsLiveReset, ttsLiveBeginTurn, ttsHexToBytes, ttsLiveFlushSentence,
    ttsLiveEnsure, ttsLiveRound, ttsLiveSend, killLiveWs, ttsLiveSay,
    ttsLiveDrainPending, ttsLiveEndRound,
  };
}
