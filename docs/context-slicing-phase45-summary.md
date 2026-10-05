# 上下文切片 P4/P5 实施总结 — 切片 × 画像记忆双向联动

日期：2026-09-13 · 分支：`feat/profile-memory`（PR #28，已含 PR #29 智能调度）

## 一、交付内容

### P4：切片级画像提取（切片完成钩子）

| 组件 | 文件 | 说明 |
|---|---|---|
| 摘要生成器 | `services/slice_summary.py` | 切片关闭时生成 `SliceSummary`：LLM 摘要（`_invoke` 咽喉层，fail-open）+ 确定性降级（用户原话拼接）；`topics` 走 `detect_topics` 闭集 |
| 完成钩子 | `services/slice_manager.py::_finalize_completed_slice` | 挂在 `get_or_create_slice` 关闭旧切片处；生成摘要 + `primary_topic` 确定性继承（有则不动、无则补齐）+ `start/end_message_id` 回填 |
| 提取溯源 | `profile_beliefs.origin_slice_id`（迁移 `20260913_0004`） | `record_claim` 新增可选参数；`run_turn_extraction` / `run_semantic_extraction` 从 `_finalize` 透传当前 `slice_id`——每条 belief 首次证据来源可回答"哪段对话提到的" |
| 开关 | `ENABLE_SLICE_BASED_EXTRACTION`（默认 false） | 独立于 P3 切片本体（摘要是额外 LLM 调用，单独灰度） |

**红线（结构性，与画像层同构）**：`safety_flag=True` 的危机轮消息整对剔除，不进摘要输入——高危内容不入切片摘要层；全危机切片的摘要行为空文本（检索侧永久过滤）。

### P5：画像驱动切片检索

| 组件 | 文件 | 说明 |
|---|---|---|
| 加权检索 | `services/slice_retrieval.py::retrieve_relevant_slices` | 得分 = **0.5 时间衰减**（§2.3.2 分段：1d/3d/7d/30d 档）+ **0.3 主题匹配**（画像 D1 keys ∪ 当前消息 detect_topics）+ **0.2 练习效果**（D4 `effect=worked` 的 tag 出现在摘要文本中）；候选池 20，返回 top-3 |
| 背景块注入 | `conversation.py::_build_state` | 检索结果渲染为【相关历史】块并入 `memory_summary`（记录层通道），**不进** `user_history_text` 情绪扫描通道——摘要不是用户当前情绪表达 |
| 开关 | `ENABLE_PROFILE_SLICE_RETRIEVAL`（默认 false） | 依赖 P4 产出摘要行；无摘要时静默跳过 |

**上下文分层（最终形态）**：
- L1 本次对话 = `slice_context`（逐字，话题边界内）→ history 消息区
- L2 相关历史 = 完成切片摘要（压缩、跨话题检索、带时间线护栏）→ memory 数据区
- L3 持久画像 = belief 流 / 结构化记录（不变，含 #29 智能调度）

## 二、关键取舍

1. **摘要同步生成而非 Celery 异步**：切片关闭本就挂在下一轮请求路径；LLM 在生成侧自带降级，最坏退化为一次词典扫描。Celery 真实接入留作部署优化。
2. **每轮提取保留 + 切片级收尾，不做"切片替代每轮"**：D2/D5/D4 与质询闭环的证据是轮粒度的（#29 价值门控也按轮节流）；切片级负责主题继承（D1）与摘要（检索供给）。两层通过 `origin_slice_id` 归因打通。
3. **主题匹配集 = D1 画像 ∪ 当前消息主题**：首轮即可命中刚聊过的主题，不必等画像沉淀出 D1（设计文档联动点 4 的增强）。
4. **检索排序只读不写**：`relevance_score` 列保持创建值，实时得分不落库（避免写放大）。

## 三、验证

- `tests/test_slice_profile_linkage_unit.py`：**21 例全过**（钩子开关回退/摘要幂等/危机过滤/边界回填/溯源写入/衰减档位/加权排序/排除当前切片/双语渲染/fail-open）
- 全量单测 **877 passed**；`ruff check` + `ruff format --check src/ tests/` 全绿
- `scripts/demo_slice_profile_linkage.py` 端到端演示（离线确定性路径）：溯源→关闭→摘要+主题继承→检索命中【相关历史】注入，数据自清理

## 四、灰度与回退

```bash
ENABLE_CONTEXT_SLICING=true          # P3 前提
ENABLE_SLICE_BASED_EXTRACTION=true   # P4（每切片边界轮 +1 次摘要 LLM 调用）
ENABLE_PROFILE_SLICE_RETRIEVAL=true  # P5（纯 DB 读，无额外 LLM）
```
三级全默认关；任一关闭即回退上一层行为，数据表与列均为增量（不锁旧路径）。

## 五、后续（未在本期）

- 画像面板"信念来源切片"详情端点（设计文档联动点 5）
- 摘要 embedding 语义检索（`summary_embedding` 列已预留）
- 切片清理策略（TTL 摘要行、belief origin_slice_id 置空）与商业化配额联动
- 真实 LLM 摘要的 Langfuse 巡检（边界轮延迟分布、摘要质量抽检）
