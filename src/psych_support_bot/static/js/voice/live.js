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

  const TTS_LIVE = {
    ws: null, failed: false, pending: [], roundStarted: false, ended: false,
    curBytes: [], curChars: 0,
    pendingTexts: [], // 已 say 未播的句子文本，随 sentence_end 出队随音频起播上屏
    pcm: false, sentenceStart: null, firstAudioMarked: false, sentenceSamples: 0, pendingEnd: false, finalTakeover: false, // PCM 流式播放态（②）+ 每句样本数（字幕按时长铺）+ 定稿接管标记
    chain: Promise.resolve(),
    roundDone: null,
    firstAudioTimeoutMs: 0, // ready.tts 下发的无首音收束预算（0=未下发，用 6s 缺省）
  };

  function ttsLiveReset() {
    TTS_LIVE.ws = null; TTS_LIVE.failed = false;
    TTS_LIVE.pending = []; TTS_LIVE.roundStarted = false; TTS_LIVE.ended = false;
    TTS_LIVE.curBytes = []; TTS_LIVE.curChars = 0; TTS_LIVE.pendingTexts = [];
    TTS_LIVE.pcm = false; TTS_LIVE.sentenceStart = null; TTS_LIVE.firstAudioMarked = false; TTS_LIVE.sentenceSamples = 0;
    TTS_LIVE.pendingEnd = false; TTS_LIVE.finalTakeover = false;
    TTS_LIVE.chain = Promise.resolve();
    TTS_LIVE.roundDone = null;
    TTS_LIVE.firstAudioTimeoutMs = 0;
    pcm.stopAll(); // 全量复位语义包含掐掉在排播的 PCM 队列
  }

  // 流式轮开拍：清上一轮残留的字幕/攒积态与降级标志。残留危害（20260912
  // 实证）：
  // - finalTakeover 残留 true → 下一轮 sentence_end 全部静默，字幕不逐句
  //   上屏，final 到达时多条信息一次性蹦出；
  // - pendingTexts 残留 → 下一轮字幕整体错位，音频读到「还没上屏的句子」；
  // - failed 残留 → 上一轮 WS 失败会让后续所有轮静默无朗读。二选一语义下
  //   朗读唯一通道是 live WS：失败只影响当轮，每轮开拍重试探路。
  function ttsLiveBeginTurn() {
    TTS_LIVE.failed = false;
    TTS_LIVE.pending = [];
    TTS_LIVE.curBytes = []; TTS_LIVE.curChars = 0;
    TTS_LIVE.pendingTexts = [];
    TTS_LIVE.sentenceStart = null; TTS_LIVE.firstAudioMarked = false; TTS_LIVE.sentenceSamples = 0;
    TTS_LIVE.pendingEnd = false; TTS_LIVE.finalTakeover = false;
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
    if (TTS_LIVE.ws && TTS_LIVE.ws.readyState === 1) return TTS_LIVE.ws;
    const ws = new WebSocketCtor(wsUrl());
    ws.binaryType = 'arraybuffer';
    ws.onmessage = (e) => {
      // 连接代次守卫：abort 后 MiniMax 取消生效前仍会泵出残余 PCM 块，
      // 旧连接在途帧若入队就是"多条信息杂乱堆叠"的串音根因——一律作废
      if (TTS_LIVE.ws && TTS_LIVE.ws !== ws) return;
      // 二进制帧 = PCM 音频块（②协议）：直接入队，首块即排定起播
      if (TTS_LIVE.pcm && e.data instanceof ArrayBuffer) {
        TTS_LIVE.sentenceSamples += e.data.byteLength / 2; // s16le 单声道 → 样本数
        const start = pcm.feed(e.data);
        if (start != null) {
          if (TTS_LIVE.sentenceStart == null) TTS_LIVE.sentenceStart = start; // 当前句的首块时刻
          if (!TTS_LIVE.firstAudioMarked) {
            TTS_LIVE.firstAudioMarked = true;
            timers.setTimeout(() => { ui.turnMark('firstAudio'); ui.turnTryReport(); },
              Math.max(0, (start - pcm.state.ctx.currentTime) * 1000));
          }
        }
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
      } else if (ev.type === 'audio' && ev.b64) {
        TTS_LIVE.curBytes.push(ttsHexToBytes(ev.b64));
        TTS_LIVE.curChars += 4; // 粗略累加字数供播放看护估算
      } else if (ev.type === 'sentence_end') {
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
        if (TTS_LIVE.pcm) {
          TTS_LIVE.ws = null;
          const done = TTS_LIVE.roundDone; TTS_LIVE.roundDone = null;
          if (done) pcm.drained().then(done); // 队列播完才收黄框
        } else {
          ttsLiveFlushSentence();
          TTS_LIVE.ws = null;
          const done = TTS_LIVE.roundDone; TTS_LIVE.roundDone = null;
          if (done) done();
        }
      } else if (ev.type === 'error') {
        debug('tts live: ' + (ev.detail || 'error'));
        // 上游会话中断（配额/风控等）：本轮剩余 say 不再朗读（文字照常由
        // final 上屏），WS 通道弃用；下一轮 ttsLiveBeginTurn 重置降级标志
        // 自动重试探路。不做运行时 HTTP 逐句回退——单句失败静默跳过是不可
        // 观测的劣化（20260912 实证：只读到最后一句）。
        TTS_LIVE.failed = true;
        TTS_LIVE.pending = [];
        TTS_LIVE.pendingTexts = [];
      }
    };
    ws.onclose = () => {
      if (TTS_LIVE.ws && TTS_LIVE.ws !== ws) return; // 废弃连接的尾巴事件
      // 连接中断：冲刷未完句，唤醒等待者（其上方 onmessage 已尽力收尾）
      ttsLiveFlushSentence();
      if (TTS_LIVE.ws === ws) TTS_LIVE.ws = null;
      const done = TTS_LIVE.roundDone; TTS_LIVE.roundDone = null;
      if (done) done();
    };
    await new Promise((resolve, reject) => {
      ws.onopen = () => resolve();
      ws.onerror = () => reject(new Error('ws open failed'));
      timers.setTimeout(() => reject(new Error('ws open timeout')), 5000);
    });
    TTS_LIVE.ws = ws;
    return ws;
  }

  // 一轮朗读（全文）：say 全文 + end，MiniMax 原生攒句，音频按 sentence_end
  // 渐进串播，round_end 后等播放链排空再 resolve（保证朗读完整）。
  // 失败向上抛，由调用方回退 HTTP 句队列。
  async function ttsLiveRound(text) {
    // 新一轮：重置上一轮残留状态（ws 在 round_end 后已被服务端关闭）。
    // revise 整轮不逐句上屏（MiniMax 分句 ≠ 我们的句），文字照旧等 final。
    TTS_LIVE.pending = [];
    TTS_LIVE.curBytes = []; TTS_LIVE.curChars = 0; TTS_LIVE.pendingTexts = [];
    TTS_LIVE.sentenceStart = null; TTS_LIVE.firstAudioMarked = false; TTS_LIVE.sentenceSamples = 0;
    TTS_LIVE.pendingEnd = false; TTS_LIVE.finalTakeover = false;
    ui.resetShownChars(); // revise 整轮重读：live 泡作废，字数预算清零
    pcm.stopAll();
    TTS_LIVE.chain = Promise.resolve();
    TTS_LIVE.roundStarted = true; TTS_LIVE.ended = true;
    await ttsLiveEnsure();
    ttsLiveSend({ type: 'say', text });
    ttsLiveSend({ type: 'end' });
    await new Promise((resolve) => {
      TTS_LIVE.roundDone = () => { TTS_LIVE.chain.then(resolve); };
    });
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
    TTS_LIVE.pending.push(text);
    TTS_LIVE.pendingTexts.push(text);
    TTS_LIVE.roundStarted = true;
    if (TTS_LIVE.ws && TTS_LIVE.ws.readyState === 1) {
      ttsLiveDrainPending();
    } else if (!TTS_LIVE.failed) {
      ttsLiveEnsure().then(ttsLiveDrainPending).catch(() => { TTS_LIVE.failed = true; pcm.stopAll(); });
    }
  }

  function ttsLiveDrainPending() {
    while (TTS_LIVE.pending.length && TTS_LIVE.ws && TTS_LIVE.ws.readyState === 1) {
      ttsLiveSend({ type: 'say', text: TTS_LIVE.pending.shift() });
    }
    // says 补发完后若有等待中的 end（握手期排队的），此刻一并送达
    if (TTS_LIVE.pendingEnd && TTS_LIVE.ws && TTS_LIVE.ws.readyState === 1) {
      TTS_LIVE.pendingEnd = false;
      ttsLiveSend({ type: 'end' });
    }
  }

  // 流式轮收轮：end + 等 round_end（此时 chain 里可能还有未播完的句，
  // 等 chain 也结束再 resolve，保证朗读完整）
  async function ttsLiveEndRound() {
    TTS_LIVE.ended = true;
    // 先 drain 任何 pending says 再发 end
    ttsLiveDrainPending();
    if (TTS_LIVE.ws && TTS_LIVE.ws.readyState === 1) {
      ttsLiveSend({ type: 'end' });
    } else {
      // WS 还在握手（投机轮 18 句+final 同帧到达的常态时序）：end 排队等
      // drain，绝不静默丢——丢了 MiniMax 不吐尾句音频、task_finished 不来，
      // 表现为"朗读读到一半没了"（2026-09-10 四段例实证）
      TTS_LIVE.pendingEnd = true;
    }
    // 等上游 round_end + 本地 chain 全播完。硬上限分档：
    // - 首音已出（firstAudioMarked）：真在播，用满 roundTimeoutMs 等尾句；
    // - 首音未出（上游挂死）：没有任何回声要防，尽快收束还麦。预算取
    //   ready.tts 下发的 firstAudioTimeoutMs（音色复刻=兼容模式流式，
    //   首包 5-20s，6s 会掐掉整轮——20260912 实证只读到最短一句）；
    //   未下发时维持 6s（预置音色口径）。
    const cap = TTS_LIVE.firstAudioMarked
      ? roundTimeoutMs
      : Math.min(roundTimeoutMs, TTS_LIVE.firstAudioTimeoutMs || 6000);
    await new Promise((resolve) => {
      const timer = timers.setTimeout(() => {
        debug('tts live: round timeout(' + cap + 'ms), force finish');
        // 挂死的会话就地弃用（打断=断连弃用的同一立场）：留着中毒 WS，
        // 下一轮 say 仍灌进旧 task，第二轮朗读继续假死
        killLiveWs(TTS_LIVE.ws);
        // 死轮的字幕队列一并清空：残留 pendingTexts 会被下一轮的
        // sentence_end 错位消费——音频读第 1 句、屏幕显示的是旧句
        TTS_LIVE.pending = [];
        TTS_LIVE.pendingTexts = [];
        TTS_LIVE.curBytes = []; TTS_LIVE.curChars = 0;
        TTS_LIVE.pendingEnd = false; TTS_LIVE.finalTakeover = false;
        resolve();
      }, cap);
      TTS_LIVE.roundDone = () => { timers.clearTimeout(timer); TTS_LIVE.chain.then(resolve); };
    });
    pcm.stopAll(); // 正常完成=空操作（源已播完）；超时兜底=掐掉滞留队列
  }

  return {
    state: TTS_LIVE, ttsLiveReset, ttsLiveBeginTurn, ttsHexToBytes, ttsLiveFlushSentence,
    ttsLiveEnsure, ttsLiveRound, ttsLiveSend, killLiveWs, ttsLiveSay,
    ttsLiveDrainPending, ttsLiveEndRound,
  };
}
