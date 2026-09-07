# 首字回复延迟基线（2026-09-01，优化前）

> 数据来源：Langfuse 线上 trace（`conversation_graph.invoke`），抽样 2026-08-29/30
> 部署后的真实对话流量（n=31，其中 support 模式 n=22）。
> 统计口径：trace.latency / observation startTime–endTime。
> 工具：`dev/latency_baseline.py`（可重跑对比）。

## 分阶段实测

| 阶段 | P50 | 最坏 | 说明 |
|---|---|---|---|
| trace 总延迟（support） | **4290ms** | 24209ms | 用户发消息 → 收到完整回复 |
| node.response_generator | — | — | 内含回复 LLM 调用 |
| reply_llm（回复生成） | **2295ms** | 13681ms | 六段 prompt，无 max_tokens 上限 |
| risk_llm（风险语义分类） | **1627ms** | — | n=6，串行在回复生成之前 |
| trace 总延迟（crisis） | ~3000ms | 3256ms | high 走 LLM 软着陆路径 |

普通 support 消息 = 2 次串行 LLM 往返（risk 1.6s + reply 2.3s ≈ 4s），
与 P50 4290ms 吻合。

## 已确认的放大器

1. **langchain 底层默认 2 次重试 × `_invoke` 咽喉层 2 次重试**——最坏 ~9 次
   底层请求。24s 离群值（total 24.2s / reply_llm 13.7s）与之吻合。
2. 无 HTTP 超时配置（langchain 默认不设）。
3. 回复生成无 `max_tokens` 上限，输出时长完全由模型决定。
4. 语言不匹配时整答重写 +1 次串行 LLM（`generation.py:146-177`）。

## 优化目标（方案见 docs/plans 或 PR 描述）

- 普通消息首答 P50 降 ≥40%（投机并行：2 次串行 → 1 次往返）。
- 最坏情况收敛（超时 + 重试唯一归咽喉层 + max_tokens 上限）。
- 危机召回 97% 不回退；critical 纯模板路径（0 LLM）不变。

## 数据质量备注

- 2026-08-31 15:48 附近的一批 trace（n≈300+）是测试套件泄漏到 Langfuse 的
  mock 数据（延迟 100-300ms 量级、消息为测试语料），统计时已排除。
  抽样统计时请按时间过滤；`latency_baseline.py` 目前需手动指定日期范围。
- 本地 `.env` 的 LLM key 于 2026-09-01 被平台治理层限制推理
  （`/models` 200 但 `/chat/completions` 403 `dots_platform_key_not_allowed`），
  危机召回 71 条语料回归暂时无法本地复跑；规则层回归
  （`scripts/eval_risk_rules_coverage.py`）不依赖 LLM，已确认无变化。
  分类判定逻辑本次零改动（仅调用位置移入并行线程），端点恢复后应无回退。
- 线上端点使用独立 key，同日探测确认正常出 LLM 回复（fallback_used=false），
  基线数据有效性不受影响。
