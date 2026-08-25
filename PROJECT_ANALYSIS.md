# Psych-Support-Bot 项目分析报告

> 审查日期：2026-08-20（初版）/ 2026-08-23（更新）
> 审查范围：全量源码 + Langfuse 集成改动 + dev 分支 PR 改动
> 审查视角：第三方独立审查

---

## 目录

- [一、项目概况](#一项目概况)
  - [1.1 定位](#11-定位)
  - [1.2 技术栈](#12-技术栈)
  - [1.3 架构全景](#13-架构全景)
  - [1.4 Langfuse 追踪结构](#14-langfuse-追踪结构本次改动成果)
- [二、整体完成度评估](#二整体完成度评估)
- [三、当前已实现的能力清单](#三当前已实现的能力清单)
  - [✅ 完整实现](#-完整实现)
  - [⚠️ 半完成](#-半完成)
  - [❌ 完全缺失](#-完全缺失)
- [四、质询机制深度分析（核心差异化能力）](#四质询机制深度分析核心差异化能力)
  - [4.1 质询的业务必要性](#41-质询的业务必要性)
  - [4.2 当前质询链路](#42-当前质询链路)
  - [4.3 单轮检测维度](#43-单轮检测维度)
  - [4.4 challenge_allowed 决策逻辑](#44-challenge_allowed-决策逻辑)
  - [4.5 Prompt 注入方式](#45-prompt-注入方式)
  - [4.6 拒答机制现状](#46-拒答机制现状)
  - [4.7 核心缺口](#47-核心缺口)
- [五、问题分类与优先级](#五问题分类与优先级)
  - [🔴 P0 — 上线必须完善](#-p0--上线必须完善安全与数据完整性硬门槛)
  - [🟡 P1 — 应该做](#-p1--应该做影响产品可用性和核心价值闭环)
  - [🟢 P2 — 可以做](#-p2--可以做增强能力不阻塞上线)
  - [🔵 P3 — 前瞻方向](#-p3--前瞻方向未来演进)
- [五、下一步建议执行顺序](#五下一步建议执行顺序)

---

## 一、项目概况

### 1.1 定位

一个基于 LangGraph 工作流的心理健康支持系统，面向轻中度心理困扰用户，提供情绪支持对话、症状筛查量表（PHQ-9/GAD-7/ISI）、引导式干预练习（CBT/ACT/DBT）、每日打卡和趋势跟踪。

**产品边界**：不提供诊断、不替代精神科医生或心理咨询师、不处理急性高危用户（自杀计划/精神病性症状/躁狂等）的深度干预。

### 1.2 技术栈

| 层 | 技术 | 状态 |
|---|---|---|
| API 框架 | FastAPI | ✅ 完整 |
| AI 编排 | LangGraph（8 节点线性图） | ✅ 完整 |
| LLM | 小红书 dots3-note-prev 模型（OpenAI 兼容接口） | ✅ 已对接 |
| 可观测性 | Langfuse SDK | ✅ 刚完成集成 |
| 数据库 | SQLAlchemy ORM + Alembic | ⚠️ SQLite 本地可用，PostgreSQL 迁移不完整 |
| 知识库 | 关键词检索 + 本地 JSON 语料 | ⚠️ pgvector 未启用 |
| 前端 | 静态 HTML 聊天页 | ⚠️ 仅聊天 |
| 缓存/队列 | Redis / Celery | ❌ 未实现（计划中） |

### 1.3 架构全景

```
用户消息
  ↓
[ConversationService.respond()]
  ├── 问卷流程拦截（assessment 优先路由）
  └── LangGraph 对话图
        ├── risk_classifier → 确定性关键词风险分类（含否定检测）
        ├── intent_router → 关键词意图路由
        ├── consultation_planner → 多流派会诊 + 临床访谈阶段策略
        ├── memory_loader → 记忆快照加载
        ├── knowledge_loader → 关键词知识检索
        ├── response_generator → LLM 生成（含语言锁 + 重试）
        ├── safety_reviewer → Prompt 泄露检测
        └── summary_writer → 会话摘要
```

### 1.4 Langfuse 追踪结构（本次改动成果）

每次对话请求在 Langfuse 中的可观测结构：

```
conversation_graph.invoke (span)
├── input: {user_id, session_id, message, mode}
├── llm.invoke (generation)
│   ├── input: {system_prompt, user_message}
│   ├── metadata: {model, language}
│   └── output: "模型回复内容..."
├── llm.invoke_retry (generation)  ← 仅语言重试时
└── output: {mode, risk_level, reply, summary}
```

改动涉及 5 个文件：

| 文件 | 改动内容 |
|---|---|
| `.env` | 新建，配置 OpenAI + Langfuse 密钥 |
| `infra/telemetry/tracing.py` | 从 40 行扩展到 132 行，新增 `get_langfuse()` / `trace_span()` / `update_span_output()` / `flush_langfuse()` |
| `services/conversation.py` | `respond()` 方法用 `trace_span` 包裹对话图调用，记录 input/output |
| `infra/llm/generation.py` | `_invoke()` 中用 `trace_span(as_type="generation")` 包裹 LLM 调用，retry 也有独立 span |
| `app.py` | lifespan 中初始化 Langfuse 客户端 + 关闭时 flush |

---

## 二、整体完成度评估

按 `PROJECT_PLAN.md` 的 6 个里程碑评估：

| 里程碑 | 状态 | 完成度 | 说明 |
|---|---|---|---|
| M0 确认范围与架构 | ✅ Done | 100% | 项目计划完整 |
| M1 后端骨架 | ✅ Done | 100% | FastAPI + 配置 + 路由 |
| M2 AI 工作流 | ✅ Done | 90% | LangGraph 8 节点完整，但 safety_reviewer 功能空心化 |
| M3 持久层 | ✅ Done | 75% | ORM 完整，但 Alembic 迁移缺表缺字段 |
| M4 核心领域 API | ✅ Done | 85% | 对话/评估/打卡/计划/报告 API 均有，但计划系统仅存根 |
| M5 安全与可观测 | 🟡 In Progress | 70% | Langfuse 集成刚完成（+10%），但 safety_reviewer 仍空心化，评估体系浅薄 |
| M6 MVP 集成 | 🟡 In Progress | 50% | 后端端到端可用，但前端仅聊天，用户旅程多链路断裂 |

**总体完成度：约 65%**

---

## 三、当前已实现的能力清单

### ✅ 完整实现

| # | 能力 | 源码位置 | 说明 |
|---|---|---|---|
| 1 | 确定性风险分类 | `ai/safety/rules.py` | 关键词匹配 + 否定检测 + 时效性升级，中英文双语 |
| 2 | 危机模式拦截 | `ai/safety/crisis.py` | critical 走纯模板；high 走 LLM + crisis safety prompt（P0-6 改动） |
| 3 | LangGraph 对话流 | `ai/graphs/conversation.py` | 8 节点线性图，风险优先路由 |
| 4 | 意图路由 | `ai/routers/intent.py` | 关键词路由到 support/assessment/intervention/planning/crisis |
| 5 | 多流派会诊 | `ai/consultation.py` + `infra/llm/generation.py` | 5 个 Agent 并行调用 + 综合，ThreadPoolExecutor 并发 |
| 6 | 临床访谈策略 | `ai/interview.py` | 检测矛盾/回避/绝对化，决定是否允许质询 |
| 7 | 量表评估全流程 | `domain/assessments/` | PHQ-9/GAD-7/ISI 完整：引导→答题→评分→解读→安全标志 |
| 8 | 每日打卡 | `api/routes/checkins.py` + 持久化 | mood/anxiety/sleep/energy 4 维 |
| 9 | 知识摄入管道 | `knowledge_ingestion.py`（852 行） | URL 抓取 + HTML 清洗 + PDF/TXT/MD 导入 + 分块 + 主题推断 + 学习笔记合成 |
| 10 | LLM 语言锁 | `infra/llm/generation.py` + `services/conversation.py` | 中文输入→中文输出，英文输入→英文输出，违反时自动重试；纯数字/标点输入回溯历史消息保持语言一致（dev 新增 `_detect_expected_language`） |
| 11 | **Langfuse 追踪** | `infra/telemetry/tracing.py` + `services/conversation.py` + `infra/llm/generation.py` + `app.py` | 对话图调用 + 每次 LLM 调用 + retry 的完整 span 链路 |
| 12 | 基础知识检索 | `ai/knowledge/index.py` | 关键词匹配 + 多维评分（mode/topics/keywords/source 加权） |
| 13 | CBT/ACT/DBT 知识库 | `ai/knowledge/{cbt,act,dbt}.py` | 认知扭曲目录 + 练习模板 + 技能指南，内容专业且详实 |
| 14 | 用户画像 API | `api/routes/users.py` + 持久化 | concerns/goals/preferences/risk_notes |
| 15 | 会话历史 API | `api/routes/conversation.py` | sessions/messages/risk-events 查询 |

### ⚠️ 半完成

| # | 能力 | 当前状态 | 缺口 |
|---|---|---|---|
| 1 | 安全审查器 | `safety_reviewer.py` 已有诊断语言/越界承诺/质询检测 + 句子级截断（P0-3/B2.3 修复） | **缺病理性归因、主观体验否定、过度病理化标签三类正则红线**；截断后无过渡衔接；fallback 不分危机场景 |
| 2 | 趋势分析 | `domain/reports/trends.py` 有前后半段均值比较 | 无异常检测、无关联评估变化、无主动预警 |
| 3 | 周报 | `domain/reports/service.py` 输出一行均值文本 | 无临床解读、无异常标记 |
| 4 | 干预计划 | `domain/plans/templates.py` 3 个静态模板 | 无每日内容、无进度跟踪、无个性化 |
| 5 | 记忆系统 | `repositories.py` 的 `build_memory_snapshot` 拼接画像+评估+打卡+最近消息 | 超过 3 个 session 的深度对话丢失上下文；无语义检索；无摘要质量校验 |
| 6 | 评估体系 | `tests/evals/cases.json` 17 个场景，仅检查路由正确性 | **无回复内容质量评测**；缺精神病性体验/悲伤非病理化/被动自杀等场景；无 LLM 评测员 |
| 7 | 前端 | `static/index.html` 4 Tab（聊天/练习/评估/打卡） | 根目录有冗余 `index.html` 造成混淆 |

### ❌ 完全缺失

| # | 能力 | 影响 |
|---|---|---|
| 1 | Onboarding 流程 | 用户无法理解产品边界、无初始安全筛查 |
| 2 | pgvector 语义检索 | 无法语义匹配，非安全路径的 RAG 能力受限 |
| 3 | 跨轮次矛盾检测 | 单轮质询无法发现"上一轮说的 A 和这一轮说的 B 矛盾"（B2.1 有关键词检测但无跨轮对比） |
| 4 | Redis/Celery 集成 | 异步摘要写入、定时周报生成无法处理 |
| 5 | 安全发布清单 | 无正式的发布前安全检查文档 |
| 6 | Langfuse 节点级追踪 | 只有顶层 span，各图节点无独立 span，无法定位瓶颈 |
| 7 | 内容质量评测集（LLM-as-judge） | 无语义级输出质量评测，无法发现"大脑扭曲感知"类问题 |

---

## 四、质询机制深度分析（核心差异化能力）

> 质询是本项目区别于"套壳 ChatGPT"的核心差异化能力。心理疾病患者的自述往往不可靠——矛盾、回避、绝对化、缩小化是常见模式。一个合格的心理支持系统不能仅做"情绪安抚器"，必须具备"质疑用户发言、发现漏洞、合理应对"的能力。

### 4.1 质询的业务必要性

在心理临床场景中，来访者的自述存在以下可靠性问题：

| 模式 | 典型表现 | 临床意义 |
|---|---|---|
| 矛盾 | "我没事，但是我真的撑不住了" | 前后表述冲突，需澄清真实状态 |
| 回避 | "随便吧""无所谓""不想说" | 可能掩盖核心痛苦，需温和探索 |
| 绝对化 | "永远""一定""完全""根本" | 认知扭曲，需检验证据和例外 |
| 缩小化 | "其实没事""也还好""不严重" | 可能与实际痛苦程度不符，需对照行为 |
| 模式重复 | "每次都这样""总是反复" | 可能有维持因素，需追踪序列 |

如果系统对这些模式无条件接受、不追问，它就只是一个"共情机器"，无法帮助用户获得真正的临床洞察。

### 4.2 当前质询链路

质询能力分布在 4 个节点的协作中：

```
用户消息
  ↓
[intent_router]  ← 关键词路由：检测危机/评估/干预/计划/支持意图
  ↓
[consultation_planner]  ← 调用 determine_interview_process()，决定访谈阶段和质询策略
  ↓
[response_generator]  ← 将策略注入 system prompt，调用 LLM 生成质询性回复
  ↓
[safety_reviewer]  ← 后置检查（目前仅检测 prompt 泄露，不审查质询合理性）
```

**关键文件**：

| 文件 | 职责 |
|---|---|
| `ai/routers/intent.py` | 关键词意图路由，含初步拒答逻辑（REFUSAL_KEYWORDS） |
| `ai/interview.py` | 核心质询逻辑：检测 8 种语言特征，决定 `challenge_allowed` |
| `ai/prompts/templates.py` | 将质询策略（`build_process_prompt`）注入 LLM system prompt |
| `ai/nodes/consultation_planner.py` | 调用 `determine_interview_process`，将结果写入 GraphState |
| `ai/nodes/response_generator.py` | 将 `challenge_allowed`/`interview_stage`/`question_strategy`/`loop_hint` 传入 LLM 生成 |

### 4.3 单轮检测维度

`ai/interview.py` 的 `determine_interview_process()` 函数检测以下 8 种语言特征：

| 维度 | 关键词示例 | 检测逻辑 |
|---|---|---|
| 开放探索 | "不知道""说不清""confused""overwhelmed" | 用户处于混乱状态，需开放提问 |
| 强模式 | "总是""每次""every time""again and again" | 反复出现的行为模式，需追踪序列 |
| 弱模式 | "一直""always""keep" | 需结合其他特征才触发模式分析 |
| 矛盾 | "但是""可是""又""but""however""yet" | 前后表述冲突，允许质询 |
| 回避 | "随便""无所谓""不想说""whatever" | 可能有可探索的回避，结合语境判断 |
| 绝对化 | "一定""永远""根本""all""never""completely" | 认知扭曲，需检验证据 |
| 缩小化 | "其实没事""也还好""不严重""it's fine" | 与实际痛苦可能不符 |
| 耗竭 | "累""疲惫""提不起劲""exhausted" | 需先澄清耗竭来源再质询 |

**关键设计决策**：

- 危机模式（`crisis`）和 high/critical 风险 → **禁止质询**，直接稳定化
- 评估模式（`assessment`）→ **禁止质询**，聚焦症状澄清
- 耗竭但无矛盾/绝对化/回避 → **禁止质询**，先探索
- 规划模式（`planning`）→ **允许质询**（`challenge_allowed = True`）

### 4.4 challenge_allowed 决策逻辑

`determine_interview_process()` 的决策优先级（后者覆盖前者）：

```
1. crisis / high / critical → safety_stabilization, 禁止质询
2. assessment → structured_assessment, 禁止质询
3. has_open_exploration → exploration, 开放提问
4. has_pattern → pattern_analysis, 循环追踪
5. has_contradiction → hypothesis_testing, 允许质询
6. has_absolutist → hypothesis_testing, 温和质询
7. has_actionable_avoidance / has_minimization → resistance_exploration, 温和质询
8. has_pattern + (contradiction/absolutist) → hypothesis_testing, 循环质询
9. has_exhaustion 且无上述 → exploration, 禁止质询（先澄清）
10. planning → planning, 允许质询
11. intervention + engagement → formulation, 澄清
```

### 4.5 Prompt 注入方式

质询策略通过 `build_process_prompt()` 注入 LLM 的 system prompt，包含：

- **访谈阶段**（`interview_stage`）：engagement / exploration / pattern_analysis / hypothesis_testing / resistance_exploration / formulation / planning / structured_assessment / safety_stabilization
- **提问策略**（`question_strategy`）：open / clarifying / looping / gentle_challenge / directive
- **质询许可**（`challenge_allowed`）：布尔值，控制 LLM 是否可质疑用户表述
- **循环提示**（`loop_hint`）：针对当前阶段的具体提问指引

这些参数在 `response_generator.py` 中传入 `generate_clinically_bounded_reply()` 或 `generate_multidisciplinary_consultation()`，最终拼接到 system prompt 中。

### 4.6 拒答机制现状

当前拒答能力分布在两个层面：

| 层面 | 实现 | 说明 |
|---|---|---|
| 意图路由层 | `ai/routers/intent.py` 的 `REFUSAL_KEYWORDS` | 检测到"跳过""不想做""算了"等，路由到 support 模式 |
| 评估流程层 | `domain/assessments/service.py` 的 `detect_skip_or_exit()` | 检测到退出意图，结束问卷会话 |

**缺口**：

- 没有针对**诊断请求**的确定性拒答。用户问"我有没有抑郁症"时，`intent.py` 不保证拦截——关键词路由可能将其归为 `support` 模式，LLM 可能自由回复一个诊断性表述
- 没有**越界承诺**的拒答。用户说"你能不能治好我"时，系统不会确定性拒绝
- 没有后置审查。`safety_reviewer` 不检查 LLM 回复中是否包含了不当诊断或越界承诺

### 4.7 核心缺口

| 缺口 | 严重度 | 说明 |
|---|---|---|
| **无跨轮次矛盾检测** | 🔴 高 | 当前 `determine_interview_process` 只分析当前这条消息，无法获取"用户上一轮说了 A，这一轮说了非 A"的矛盾。`memory_loader` 节点仅拼接文本快照，不做结构化对比。这导致最核心的质询场景（前后矛盾）无法实现 |
| **无诊断请求确定性拦截** | 🔴 高 | 用户直接问"我是不是抑郁症""我是不是焦虑症"时，`intent.py` 关键词不包含诊断请求检测，LLM 可能给出诊断性回复，违反产品安全边界 |
| **质询合理性无后置审查** | 🟡 中 | `safety_reviewer` 仅检测 prompt 泄露，不检查 LLM 的质询是否过度激进、是否在不应质询的场景下质询了 |
| **单轮关键词检测的精度局限** | 🟡 中 | "但是"可能是真正的转折而非矛盾，"永远"可能是修辞而非认知扭曲。纯关键词匹配无法区分语用差异，可能导致过度质询 |
| **耗竭检测过粗** | 🟢 低 | 当前"累""疲惫"等词触发耗竭路径，但无法区分身体疲劳、情绪耗竭、关系疲惫和预期性焦虑——临床意义完全不同 |

---

## 五、问题分类与优先级

### 🔴 P0 — 上线必须完善（安全与数据完整性硬门槛）

| # | 问题 | 源码位置 | 风险说明 |
|---|---|---|---|
| 1 | **`response_generator.py` fallback bug** | 第 57-60 行：`raise RuntimeError` 后 `state["fallback_used"] = True` 不可达 | LLM 调用失败时服务直接崩溃，用户无任何回复 |
| 2 | **Alembic 迁移不完整** | `questionnaire_sessions` 表无迁移；`assessments` 表缺 5 个字段（`plain_meaning`/`functional_impact`/`care_consideration`/`disclaimer`/`needs_safety_followup`） | PostgreSQL 生产环境跑 Alembic 后问卷功能崩溃 |
| ~~3~~ | ~~**safety_reviewer 空心化**~~ | ✅ 已修复（P0-3/B2.3）：已加诊断语言/越界承诺/质询检测 + 句子级截断。**但缺病理性归因/主观体验否定/过度病理化三类正则** |
| ~~4~~ | ~~**.env 密钥泄露风险**~~ | ✅ 已修复：.gitignore + CI secrets-check |
| ~~5~~ | ~~**无诊断请求确定性拦截**~~ | ✅ 已修复（P0-5）：`intent.py` 已加 `DIAGNOSIS_KEYWORDS`，`generation.py` 注入诊断拒答 prompt |
| 6 | **病理性归因正则缺失** | `safety_reviewer.py` 现有正则不匹配"大脑扭曲感知""你的感知不真实"等表述 | LLM 自行生成的病理性归因不会被拦截，对精神病性用户造成二次伤害 |
| 7 | **主观体验否定正则缺失** | `safety_reviewer.py` 不匹配"你看到的不存在""你听到的不是真的"等 | 否定用户主观体验，破坏信任，加剧孤立感 |
| 8 | **截断后无软着陆过渡** | `_sanitize_text` 删违规句后直接输出剩余内容 | 回复可能断裂，用户体验突兀；fallback 不区分危机场景 |

### 🟡 P1 — 应该做（影响产品可用性和核心价值闭环）

| # | 问题 | 说明 |
|---|---|---|
| ~~6~~ | ~~**前端功能补全**~~ | ✅ 已完成：`static/index.html` 4 Tab（聊天/练习/评估/打卡） |
| 7 | **跨轮次矛盾检测** | B2.1 有关键词检测但无跨轮对比。详见[质询机制分析 §4.7](#47-核心缺口) |
| 8 | **干预计划内容填充** | 3 个计划模板仅存目录，无每日步骤和进度跟踪 |
| ~~9~~ | ~~**质询合理性后置审查**~~ | ✅ 已完成（B2.3）：`safety_reviewer` 在 `challenge_allowed=False` 时截断质询语言 |
| 10 | **周报临床化** | 仅均值统计无异常检测，不具备早期预警能力 |
| 11 | **评估体系深化** | 补充精神病性体验/悲伤非病理化/被动自杀等场景，增加回复内容质量评估（正则 + LLM-as-judge） |
| 12 | **安全发布清单** | 无正式的发布前安全检查文档 |
| 13 | **Langfuse 节点级追踪** | 只有顶层 span，各图节点无独立 span |
| 14 | **清理冗余 index.html** | 根目录 `index.html` 与 `static/index.html` 重复，造成混淆 |

### 🟢 P2 — 可以做（增强能力，不阻塞上线）

| # | 方向 | 说明 |
|---|---|---|
| 12 | **pgvector 语义检索** | 当前关键词检索在非安全路径已够用，语义检索可提升匹配质量但不能作为安全门槛 |
| 13 | **LLM 模型路由** | 简单支持用轻量模型，复杂会诊用强模型，降低成本和延迟 |
| 14 | **LLM 降级策略** | 主模型不可用时 fallback 到模板回复，保证服务连续 |
| 15 | **知识库中文化** | 当前摄入源全为英文 NIMH/MedlinePlus，补充中文权威来源 |
| 16 | **Redis/Celery 集成** | 异步摘要写入、定时周报生成 |
| 17 | **耗竭检测精细化** | 区分身体疲劳/情绪耗竭/关系疲惫/预期性焦虑，提升质询精度。详见[质询机制分析 §4.7](#47-核心缺口) |

### 🔵 P3 — 前瞻方向（未来演进）

| # | 方向 | 说明 |
|---|---|---|
| 18 | **Langfuse Score 评估闭环** | 在 Langfuse 中对每次对话做人工/AI 评分，形成"trace → score → 优化"闭环 |
| 19 | **Relapse 预警** | 基于 check-in 趋势 + 评估历史，预测复发风险并主动干预 |
| 20 | **个性化策略路由** | 根据用户画像和历次效果数据，动态选择最优干预流派 |
| 21 | **单轮关键词检测精度提升** | 引入 LLM 辅助的语用分析，区分"但是"是真正转折还是修辞。详见[质询机制分析 §4.7](#47-核心缺口) |
| 22 | **多语言扩展** | 当前仅中英双语，可扩展日韩等 |
| 23 | **Admin 后台** | 风险事件审计、内容质量审查、用户管理 |

---

## 2026-08-23 更新说明

本次更新基于 dev 分支（`0253a93`）的全面审查，主要变更：

### 已修复的 P0 项（打删除线标记）

| 原编号 | 问题 | 修复提交 |
|---|---|---|
| P0-1 | response_generator fallback bug | `6e5b263` |
| P0-2 | Alembic 迁移不完整 | `29deaa3` |
| P0-3 | safety_reviewer 空心化 | `222aa44`（诊断/越界检测 + 句子级截断） |
| P0-4 | .env 密钥泄露 | .gitignore + CI secrets-check |
| P0-5 | 无诊断请求拦截 | `b1e3d58`（DIAGNOSIS_KEYWORDS + 拒答 prompt） |
| P0-6 | high 风险走纯模板 | `5846e99`（high 走 LLM + crisis safety prompt） |
| P0-7 | crisis 关键词过于宽泛 | `b2737a0`（移除 "help me"） |
| P0-16 | 无 mode-based temperature | `aca2888`（crisis=0.0, support=0.4 等） |

### 新增缺口（本次审查发现）

| # | 缺口 | 优先级 | 说明 |
|---|---|---|---|
| 新-1 | 病理性归因正则缺失 | P0 | LLM 生成"大脑扭曲感知"等表述不被拦截 |
| 新-2 | 主观体验否定正则缺失 | P0 | "你看到的不存在"等表述不被拦截 |
| 新-3 | 过度病理化标签正则缺失 | P0 | "这是幻觉""这是妄想"等表述不被拦截 |
| 新-4 | 截断后无过渡衔接 | P0 | 删违规句后回复可能断裂 |
| 新-5 | fallback 不分场景 | P0 | 高危截断全空时应用 crisis 模板 |
| 新-6 | 内容质量评测集缺失 | P1 | 现有 eval 只检查路由，不检查 reply text |
| 新-7 | LLM-as-judge 评测员缺失 | P1 | 正则测不到的语义问题需要 LLM 语义判断 |
| 新-8 | 评测场景覆盖不足 | P1 | 缺精神病性体验/悲伤非病理化/被动自杀等 |
| 新-9 | 安全发布清单缺失 | P1 | 无正式发布前检查文档 |
| 新-10 | Langfuse 节点级追踪缺失 | P1 | 只有顶层 span，无法定位节点瓶颈 |
| 新-11 | 根目录冗余 index.html | P1 | 与 static/index.html 重复 |

### 记忆系统评估更新

原报告将记忆系统标记为"非结构化拼接"，经详细审查 `build_memory_snapshot` 实现，更新评估：

- 实际已聚合：用户画像 + 最近会话摘要 + 最近 3 个 session 的 6 条消息 + 最近 3 次评估结果 + 最近 3 次打卡均值
- 对于早期心理疏导的 MVP 阶段，此设计**合理**——token 可控，核心上下文不丢
- 真正缺口在于：超过 3 个 session 的深度对话会丢失早期上下文；无语义检索；无摘要质量校验
- 这些属于 P2 级别，不阻塞早期疏导业务

---

## 六、下一步建议执行顺序

```
已完成（原 P0）
  ├── ✅ 1. 修复 response_generator.py fallback bug（P0-1）
  ├── ✅ 2. 补全 Alembic 迁移（P0-2）
  ├── ✅ 3. 增强 safety_reviewer：诊断/越界/质询检测 + 句子级截断（P0-3/B2.3）
  ├── ✅ 4. .env 加入 .gitignore + CI secrets-check（P0-4）
  ├── ✅ 5. 诊断请求确定性拦截（P0-5）
  ├── ✅ 6. high 风险走 LLM + crisis prompt（P0-6）
  ├── ✅ 7. crisis 关键词对齐（P0-7）
  ├── ✅ 8. mode-based temperature（P0-16）
  ├── ✅ 9. 跨轮次矛盾检测关键词（B2.1）
  ├── ✅ 10. 前端 4 Tab 界面（C1-C4）
  ├── ✅ 11. 语言一致性 + 问卷解析增强
  └── ✅ 12. Docker 部署 + 阿里云镜像源

立即修复（新 P0）
  ├── 1. 病理性归因正则红线
  ├── 2. 主观体验否定正则红线
  ├── 3. 过度病理化标签正则红线
  ├── 4. 截断后软着陆过渡衔接
  └── 5. fallback 分场景（危机 vs 普通）

短期补足（P1）
  ├── 6. 评测集场景扩展（精神病性/悲伤/被动自杀等）
  ├── 7. 内容质量评测（正则层：红线 + 结构 + 语言）
  ├── 8. 内容质量评测（LLM-as-judge 层）
  ├── 9. 安全发布清单文档
  ├── 10. Langfuse 节点级 span + flush
  ├── 11. 清理根目录冗余 index.html
  ├── 12. 跨轮次矛盾检测（结构化对比）
  ├── 13. 干预计划每日内容填充
  └── 14. 周报异常检测逻辑

中期增强（P2）
  ├── 15. pgvector 语义检索（非安全路径）
  ├── 16. 模型路由 + 降级策略
  ├── 17. 知识库中文源补充
  ├── 18. Redis/Celery 异步任务
  ├── 19. 耗竭检测精细化
  └── 20. 记忆检索语义化（超过 3 session 的深度对话）

长期演进（P3）
  ├── 21. Langfuse Score 闭环
  ├── 22. Relapse 预警模型
  ├── 23. 个性化策略路由
  ├── 24. 单轮关键词检测精度提升（LLM 辅助语用分析）
  ├── 25. 多语言扩展
  └── 26. Admin 审计后台
```

---

*本报告基于全量源码审查生成，如需对某个具体问题展开方案设计，请单独提出。*
