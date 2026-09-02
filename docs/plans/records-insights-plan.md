# 记录与洞察功能线实施计划（功能 3 → 4 → 1 + 商业化锚点）

> 状态：待实施 · 2026-08-31 制定
> 前置阅读：本计划基于 Langfuse 巡检后主线（`pr4/safety-hardening-llm-risk`，PR #19）
> 约定：所有 LLM 分析走 `_invoke` 咽喉层（重试+声明式降级），失败时确定性文本兜底；
> AI 分析统一独立端点（付费墙锚点）；等待体验复用回声气泡。

## 目标与结论摘要

为练习/评估/打卡三条线补齐「记录查看 + AI 简要分析」闭环。价值评估结论：
- 功能 3（问卷历史）：**性价比最高**，数据层 90% 现成
- 功能 4（打卡趋势）：**数据基础最好**，CheckinRecord 四维现成，缺查询 API
- 功能 1（练习记录）：**价值最高但缺口最大**，需从建表开始（当前练习完全不落库）
- 功能 2（练习翻译）：不做中英对照（认知负担），转练习内容**中文化**，归并入功能 1
- 商业化：AI 解读次数为付费锚点，记录查看保持免费；计量埋点随本计划一并落地

## M1：问卷历史 + AI 解读（功能 3，约 1.5 天）

数据层（几乎现成）：
- [ ] Alembic 迁移：`assessments` / `questionnaire_sessions` 加 `source` 列（`chat`/`panel`，默认 `chat`，仅来源分析用，不分割记录）
- [ ] 对话内问卷落库处写入 `source="chat"`

API：
- [ ] `GET /api/v1/assessments` —— 历史列表（repository 已有查询，补路由 + schema）
- [ ] `GET /api/v1/assessments/analysis` —— **独立端点**（付费墙锚点）；LLM 读历次分数/band/时间间隔生成三句内解读（趋势方向 + 安全信号提示 + 一个具体建议）
- [ ] 降级：LLM 失败 → 确定性文本（"N 次测评，最近 X 分，较上次 ±N，band 分布"）
- [ ] 埋点：`assessment_submitted`（复用已有落库路径）、`ai_analysis_requested/served`

前端（评估 Tab）：
- [ ] 历史记录列表（时间/量表/分数/band/来源）
- [ ] AI 解读卡（带"生成解读"按钮 → 回声气泡等待 → 展示）
- [ ] 趋势线（对话里已有的 `format_trend_line` 口径复用）

测试：
- [ ] 路由/降级/埋点单测 + 集成测试（含 LLM 失败 fail-safe）

## M2：打卡记录 + 心情趋势（功能 4，约 1.5 天）

数据层（100% 现成，无迁移）：
- [ ] 零新表；`usage_events` 表在本里程碑建（见商业化锚点节）

API：
- [ ] `GET /api/v1/checkins?days=30` —— 历史查询路由（当前只有 POST）
- [ ] `GET /api/v1/checkins/trend` —— 结构化趋势数据（供前端画图：日期/mood/anxiety/sleep/energy 序列）
- [ ] `GET /api/v1/checkins/analysis` —— **独立端点**；LLM 读 30 天数据 → 规律发现（睡眠-心情关联、周内波动）+ 确定性降级（均值/方向）
- [ ] 埋点：`checkin_created`

前端（打卡 Tab）：
- [ ] 记录列表（日期 + 四维 + note）
- [ ] 手写 SVG 折线趋势图（mood 主轴 + anxiety 对比，不引重型图表库）
- [ ] AI 趋势解读卡（回声气泡等待模式）

测试：同 M1 模式。

## M3：练习记录 + AI 分析 + 练习中文化（功能 1 + 2，约 4 天）

数据层（新建）：
- [ ] 新表 `exercise_records`：user_id / exercise_tag / source（chat/panel）/ reflection_note / completed_at
- [ ] Alembic 迁移 + repository（create/list/by_user）
- [ ] 对话图联动：对话中完成练习时自动落库（`exercise_history` 内存字段 → 持久化来源），`source="chat"`

API：
- [ ] `POST /api/v1/exercises/{tag}/complete` —— 完成上报（页面练习用，`source="panel"`）
- [ ] `GET /api/v1/exercises/records` —— 历史
- [ ] `GET /api/v1/exercises/records/analysis` —— **独立端点**；LLM 读最近 N 次（类型分布/频率/间隔/反思笔记）→ 简要分析 + 下一步建议；确定性降级（"本周完成 X 次，以呼吸类为主"）
- [ ] 埋点：`exercise_completed` + AI 分析双事件

前端（练习 Tab）：
- [ ] 练习记录列表 + AI 分析卡
- [ ] 练习内容中文化：exercises 库按 `expected_language` 出对应语言版本（一次性内容工作，不做中英对照）

测试：迁移/上报/联动/降级/埋点全覆盖。

## 商业化锚点（贯穿 M1-M3）

- [ ] `usage_events` 表（user_id / event_type / created_at / metadata_json）+ Alembic 迁移（M2 建，M1 起的埋点先打日志、M2 落表后补录无妨——或直接 M1 建，推荐）
- [ ] 事件枚举固定：`exercise_completed` / `assessment_submitted` / `checkin_created` / `ai_analysis_requested` / `ai_analysis_served`
- [ ] 付费墙预留：所有 AI 分析为独立 `/analysis` 端点，将来配额检查（`check_quota(user_id, "ai_analysis")`）是单点中间件
- [ ] **伦理边界（硬约束）**：埋点只记动作元数据（何时/何类型/次数），不记情绪内容画像——mood 分数、note、练习反思不进商业化分析；写入本仓库 README/隐私声明

## 里程碑顺序与理由

1 → 2 → 3：先拿两个"数据层现成"的快赢（M1/M2 各 1.5 天）建立记录页 + AI 解读卡 + 埋点的完整页面模式，M3 照抄模式只重做数据层；避免一上来在练习表设计上消耗。

## 明确不做（本期）

- 中英对照练习文本（认知负担，只做按语言出单版本）
- 记录查看的付费墙（留存抓手，保持免费）
- 重型图表库 / 独立事件系统（SVG 手写 + 单表打点足够）
- 情绪内容画像 / 商业化数据分析（伦理边界）
