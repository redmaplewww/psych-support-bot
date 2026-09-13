# 语音链路工程决策记录（ADR 摘要）

语音 I/O 链路（ASR → LLM → TTS）的状态与边界约束。改代码前先读这里；
改约束时先改这里。协议细节见 [VOICE_PROTOCOL.md](VOICE_PROTOCOL.md)。

## D1. 单 worker 约束（当前生效）

**决策**：uvicorn 以单进程运行（`Dockerfile.server` CMD 未设 `--workers`，
缺省 1；Makefile `serve` 同）。**扩 worker 前必须先做 D1.1。**

**原因**：两个进程内有意的运行时状态——

| 状态 | 位置 | 多 worker 下的退化 |
|---|---|---|
| TTS 文本缓存（LRU+TTL 64 条） | `infra/voice/adapter.py` | 命中率按 worker 数摊薄（backchannel/练习须知等固定文案重复打上游，成本↑） |
| STT 故障看门狗（streak + degraded TTL） | `api/routes/voice.py` | 各 worker 独立计数：前端 `/status` 看到的 degraded 标志取决于路由到哪台，降级预期不稳定 |

两者影响都是"性能/体验退化"而非"正确性损坏"，所以当前选择文档化约束而非
引入共享存储。**D1.1**（扩容前置条件）：缓存迁 Redis（栈内已有 Redis 会话
基建），看门狗迁 Redis 计数器；两者都改完才允许 `--workers > 1`。

## D2. 服务端限流（待决，倾向做）

**现状**：`/v1/voice/transcribe`、`/speak*`、`/tts/live`、`/backchannel*`
只靠鉴权挡；静音闸门是纯客户端开关（`localStorage`）——善意用户语义，
不是滥用防线。登录用户可打爆 TTS 成本（自由文本无缓存兜底）。

**倾向方案**：按 user_id 的进程内 token bucket（与 D1 一致：单 worker 下
进程内限流有效；多 worker 时随 D1.1 一并迁 Redis）。建议初值：TTS 合成
30 次/分钟/用户、STT 20 次/分钟/用户，超限 429。**尚未实现**——等出现
真实的第三方部署/公网暴露再做；内网自用阶段优先级低于成本开关（静音键）。

## D3. 上游重试与超时的对齐关系（2026-09-11 固化）

三方数值互相耦合，改任何一个必须复核其余两个（详见 VOICE_PROTOCOL.md
超时对齐表）：

- 服务端 MiniMax live 上游 recv 空闲 30s（`_TTS_LIVE_UPSTREAM_RECV_TIMEOUT`）
- 前端 round 收束硬上限 25s（`static/js/voice/live.js` `ROUND_TIMEOUT_MS`）
- MiMo HTTP 流 read 8s（`adapter._mimo_stream`）

原则：**前端 ≤ 服务端**——上游挂死时前端先收束，麦克风不被黄框假死挂住。
重试策略：非流式 POST 由 `_post_with_retry` 统一（429/5xx/网络错误退避
0.8s 单次重试，全供应商一致）；流式请求**不适用**整体重试（中途失败重试
= 重复产出，`_mimo_stream` 以 emitted 计数守恒）。

## D4. 前端语音模块的结构约束（2026-09-11 固化）

`static/js/voice/`（protocol/pcm/live）是 IIFE 内动态 import 接线的
ES modules，可被 `node --test tests/frontend/` 单测。约束：

- 模块内部不得直接引用 `window`/`localStorage`/DOM——一切外部依赖走
  `createXxx(deps)` 注入（WS 构造器、定时器、AudioContext、UI 回调）。
  node 环境恰好有全局 `WebSocket`（undici），不注入会真联网。
- 接线块沿用 index.html 原符号名，模块就绪前的安全桩语义：failed=true →
  有声轮走 HTTP 句队列、final 即时渲染（竞态窗口可恢复降级，不崩）。
- 协议常量与 `infra/voice/protocol.py` 同名同源，改协议四处同步
  （protocol.py / protocol.js / schema 导出 / 协议文档），对端测试会红。
- 分句两端语义由 `tests/frontend/fixtures/sentences.json` 钉住，改动即红。

## D5. 音频隐私边界（不可妥协）

音频即转即弃：不落库、不写日志、无临时文件。转写文本走既有消息持久化
边界（与打字输入同权）。任何"缓存音频/暂存转写"的优化提案默认否决，
除非同时给出加密存储与保留期限方案。

## D6. 对话朗读二选一：WS live 是唯一通道（20260912）

**决策**：对话轮的朗读只走 WS `/tts/live`（整轮单会话，PCM 首块即播）。
前端 HTTP 句队列（TTS_QUEUE/预取/逐句跳过）整体删除；`/speak`、
`/speak/stream` 标记废弃，仅为旧前端保留一个版本后下线。

**原因**：运行时双路径的降级是"静默逐句跳过"——单句失败只写一行 debug
就丢，用户听到的是残缺朗读且无任何显性信号（20260912 实证：三分句回复
只读出最后一句）。这违背 D3/看门狗哲学（静默故障要吵闹）。双路径还带来
粘性失败（WS 一次失败→整页会话降级）与状态机复杂度（TTS_QUEUE ×
TTS_LIVE × prefetch 交织）。

**新失败语义**：WS 失败=当轮无声（文字照常上屏）+ 显式日志；`failed`
标志只在当轮有效，每轮开拍 `ttsLiveBeginTurn()` 重置——下一轮自动重试
探路。一轮没声音可感知、可恢复；静默吞半段不可感知、不可恢复。

**HTTP 保留的真实用途**：backchannel 预取（`/backchannel/{i}`，短固定语
闲时取好、播放零延迟）与 `/status` 探测——这是 HTTP 语义的正确场景。

