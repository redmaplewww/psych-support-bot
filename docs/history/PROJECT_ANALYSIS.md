# Psych-Support-Bot 项目分析报告

> 审查日期：2026-08-20
> 审查范围：全量源码 + Langfuse 集成改动
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
- [五、知识库内容覆盖层面全景](#五知识库内容覆盖层面全景)
  - [5.1 心理问题主题层面](#51-心理问题主题层面)
  - [5.2 心理干预理论层面](#52-心理干预理论层面)
  - [5.3 意图识别与路由层面](#53-意图识别与路由层面)
  - [5.4 会诊机制层面](#54-会诊机制层面)
  - [5.5 结构化面试与干预过程层面](#55-结构化面试与干预过程层面)
  - [5.6 辅助功能层面](#56-辅助功能层面)
- [六、产品方向可行性评估](#六产品方向可行性评估)
  - [6.1 方向一：主打轻度心理问题覆盖大众场景](#61-方向一主打轻度心理问题覆盖大众场景)
  - [6.2 方向二：拒达＋意图识别配合个人专项练习方案](#62-方向二拒达意图识别配合个人专项练习方案)
  - [6.3 方向三：增加"练习工具"入口](#63-方向三增加练习工具入口)
  - [6.4 三方向协同关系与综合结论](#64-三方向协同关系与综合结论)
- [七、问题分类与优先级](#七问题分类与优先级)
  - [🔴 P0 — 上线必须完善](#-p0--上线必须完善安全与数据完整性硬门槛)
  - [🟡 P1 — 应该做](#-p1--应该做影响产品可用性和核心价值闭环)
  - [🟢 P2 — 可以做](#-p2--可以做增强能力不阻塞上线)
  - [🔵 P3 — 前瞻方向](#-p3--前瞻方向未来演进)
- [八、下一步建议执行顺序](#八下一步建议执行顺序)

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
| 2 | 危机模式拦截 | `ai/safety/crisis.py` | critical/high 风险跳过 LLM，直接输出固定模板安全回复 + 热线。⚠️ high 级别也完全不走 LLM，被动自杀意念与主动计划得到完全相同的模板回复，缺乏针对用户消息的共情回应 |
| 3 | LangGraph 对话流 | `ai/graphs/conversation.py` | 8 节点线性图，风险优先路由 |
| 4 | 意图路由 | `ai/routers/intent.py` | 关键词路由到 support/assessment/intervention/planning/crisis |
| 5 | 多流派会诊 | `ai/consultation.py` + `infra/llm/generation.py` | 5 个 Agent 并行调用 + 综合，ThreadPoolExecutor 并发 |
| 6 | 临床访谈策略 | `ai/interview.py` | 检测矛盾/回避/绝对化，决定是否允许质询 |
| 7 | 量表评估全流程 | `domain/assessments/` | PHQ-9/GAD-7/ISI 完整：引导→答题→评分→解读→安全标志 |
| 8 | 每日打卡 | `api/routes/checkins.py` + 持久化 | mood/anxiety/sleep/energy 4 维 |
| 9 | 知识摄入管道 | `knowledge_ingestion.py`（852 行） | URL 抓取 + HTML 清洗 + PDF/TXT/MD 导入 + 分块 + 主题推断 + 学习笔记合成 |
| 10 | LLM 语言锁 | `infra/llm/generation.py` | 中文输入→中文输出，英文输入→英文输出，违反时自动重试 |
| 11 | **Langfuse 追踪** | `infra/telemetry/tracing.py` + `services/conversation.py` + `infra/llm/generation.py` + `app.py` | 对话图调用 + 每次 LLM 调用 + retry 的完整 span 链路 |
| 12 | 基础知识检索 | `ai/knowledge/index.py` | 关键词匹配 + 多维评分（mode/topics/keywords/source 加权） |
| 13 | CBT/ACT/DBT 知识库 | `ai/knowledge/{cbt,act,dbt}.py` | 认知扭曲目录 + 练习模板 + 技能指南，内容专业且详实 |
| 14 | 用户画像 API | `api/routes/users.py` + 持久化 | concerns/goals/preferences/risk_notes |
| 15 | 会话历史 API | `api/routes/conversation.py` | sessions/messages/risk-events 查询 |

### ⚠️ 半完成

| # | 能力 | 当前状态 | 缺口 |
|---|---|---|---|
| 1 | 安全审查器 | `safety_reviewer.py` 仅检测 Prompt 泄露，且检测到泄露时**整条回复被一刀切替换**为固定兜底语句（第 68-72 行），不截断到泄露点而是全量丢弃 | 不检查诊断语言/不当建议/安全边界越界；好回复仅因末尾误带标记词就被全量替换 |
| 2 | 趋势分析 | `domain/reports/trends.py` 有前后半段均值比较 | 无异常检测、无关联评估变化、无主动预警 |
| 3 | 周报 | `domain/reports/service.py` 输出一行均值文本 | 无临床解读、无异常标记 |
| 4 | 干预计划 | `domain/plans/templates.py` 3 个静态模板 | 无每日内容、无进度跟踪、无个性化 |
| 5 | 记忆系统 | `repositories.py` 的 `build_memory_snapshot` 拼接快照 | 非结构化、无跨轮次矛盾检测、无用户可见控制 |
| 6 | 评估体系 | `tests/evals/cases.json` 17 个场景 | 缺 3 个计划维度，无回复质量评估 |

### ❌ 完全缺失

| # | 能力 | 影响 |
|---|---|---|
| 1 | Onboarding 流程 | 用户无法理解产品边界、无初始安全筛查 |
| 2 | 前端界面（除聊天外） | 评估/练习/打卡/报告/计划对用户不可见 |
| 3 | pgvector 语义检索 | 无法语义匹配，非安全路径的 RAG 能力受限 |
| 4 | 跨轮次矛盾检测 | 单轮质询无法发现"上一轮说的 A 和这一轮说的 B 矛盾" |
| 5 | 诊断请求确定性拦截 | 用户说"我是不是抑郁症"时，意图路由不保证拦截诊断请求 |
| 6 | Redis/Celery 集成 | 异步摘要写入、定时周报生成无法处理 |
| 7 | LLM 降级策略 | `response_generator.py` 第 59 行 `raise RuntimeError` 后的 fallback 代码不可达 |
| 8 | Temperature=0.0 回复同质化 | `infra/llm/factory.py` 第 13 行设 `temperature=0.0`，每次对相同输入几乎生成相同回复，用户反复说"我还是很难受"时连续三轮得到近乎一样的回复 |

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
| **无跨轮次风险追踪** | 🔴 高 | `risk_classifier` 仅看当前 `user_message`，不读取 `memory_summary` 或对话历史。用户第一轮说"我最近很累"被判 low，第二轮说"活着没意思"单独判 elevated——但合在一起应升到 high。连续两轮 elevated 以上也无自动升级机制 |
| **无诊断请求确定性拦截** | 🔴 高 | 用户直接问"我是不是抑郁症""我是不是焦虑症"时，`intent.py` 关键词不包含诊断请求检测，LLM 可能给出诊断性回复，违反产品安全边界 |
| **意图路由与风险分类关键词冲突** | 🟡 中 | `ai/routers/intent.py` 的 `CRISIS_KEYWORDS` 与 `ai/safety/rules.py` 的 `HIGH_RISK_KEYWORDS` 独立维护且存在重叠/冲突。例如 "help me" 在 `intent.py` 中被归为危机关键词（过于宽泛），但 `rules.py` 不认为它是高风险。`risk_classifier` 先跑并可能设置 `mode=crisis`，导致 `intent_router` 被跳过；反过来 `risk_classifier` 未触发 crisis 时 `intent_router` 可能因 "help me" 设 crisis 但 `risk_level` 仍是 low/elevated，两者不一致 |
| **质询合理性无后置审查** | 🟡 中 | `safety_reviewer` 仅检测 prompt 泄露，不检查 LLM 的质询是否过度激进、是否在不应质询的场景下质询了 |
| **单轮关键词检测的精度局限** | 🟡 中 | "但是"可能是真正的转折而非矛盾，"永远"可能是修辞而非认知扭曲。纯关键词匹配无法区分语用差异，可能导致过度质询 |
| **否定检测过于粗糙** | 🟡 中 | `rules.py` 的否定检测是布尔子串匹配，无语法分析。存在以下具体缺陷：①"以前想过自杀但现在不想了"同时命中 HIGH_RISK 和否定词，被降为 elevated，但"曾经想过自杀"本身需要关注；②"没有不想活"等双重否定无法处理；③无时态概念——"我昨天想自杀"和"我现在想自杀"权重相同；④中文否定靠词表覆盖，"想死"作为关键词同时出现在否定词"不想死"中，匹配逻辑脆弱 |
| **Temperature=0.0 导致回复同质化** | 🟡 中 | `infra/llm/factory.py` 第 13 行设 `temperature=0.0`，每次对相同输入几乎生成相同回复。心理支持场景中用户反复说"我还是很难受"时连续三轮得到近乎一模一样的回复，缺乏对话进展感，用户体验机械生硬 |
| **耗竭检测过粗** | 🟢 低 | 当前"累""疲惫"等词触发耗竭路径，但无法区分身体疲劳、情绪耗竭、关系疲惫和预期性焦虑——临床意义完全不同 |

---

## 五、知识库内容覆盖层面全景

> 本章对源码中的全部知识内容进行系统化梳理，按 6 个层面逐一展开，为后续产品方向可行性评估提供事实基础。

### 5.1 心理问题主题层面

源码在 `ai/knowledge/index.py` 的 `TOPIC_KEYWORDS` 字典中定义了 **17 个主题域**，每个主题包含中英文双语触发词，由 `detect_topics()` 函数对用户消息进行 Top-3 主题匹配：

| 类别 | 覆盖的主题 | 说明 |
|---|---|---|
| **轻度 / 大众场景** ✅ | `anxiety`(焦虑)、`procrastination`(拖延)、`rumination`(反刍/胡思乱想)、`motivation`(没动力/冻结)、`stress`(压力)、`burnout`(倦怠)、`sleep`(失眠)、`self_worth`(自我价值/自责)、`social_anxiety`(社恐)、`relationships`(亲密关系/孤独/人际冲突) | 涵盖工作、学习、亲密关系等大众高频场景 |
| **中度场景** | `depression`(抑郁)、`panic`(惊恐发作)、`ocd`(强迫症)、`anger`(愤怒)、`grief`(哀伤/丧失) | 有完整知识条目和干预指南，但场景偏临床 |
| **重度 / 危机场景** | `ptsd`(创伤后应激)、`eating_disorder`(进食障碍)、`nssi`(非自杀性自伤)、`substance_use`(物质依赖) | 有基础知识条目，但产品定位明确不深度处理 |

**关键发现**：用户提出的"拖延、焦虑、不专注"属于轻度场景，其中拖延和焦虑已在知识库中完整覆盖。`procrastination` 主题的触发词包括"拖延""逃避""开始不了""卡住了""can't start"等，`motivation` 主题的触发词包括"没动力""提不起劲""停摆""冻结住了""freeze""shutdown"等，覆盖面较广。

**当前缺口**："不专注 / 注意力不集中"尚未独立存在于 `TOPIC_KEYWORDS` 中。当前部分被 `rumination` 主题覆盖（如"脑子停不下来""钻牛角尖"），但缺少"无法集中""走神""分心""注意力""concentration""distracted""can't focus"等直接的专注力相关触发词。此外"工作""学习"场景缺乏细化触发词（如"deadline""考试""加班""赶项目"等）。

### 5.2 心理干预理论层面

源码集成了 **5 大治疗流派** 的完整知识体系，每个流派包含理论框架、结构化练习和干预指南：

| 流派 | 源码文件 | 练习数 | 干预指南 | 核心能力 |
|---|---|---|---|---|
| **CBT**（认知行为疗法） | `ai/knowledge/cbt.py`（624 行） | 7 个 | 7 个主题指南 | 认知歪曲目录（11 种）+ 想法记录 + 行为激活 + 担忧树 + 成本效益分析 + 自悯信 |
| **ACT**（接纳承诺疗法） | `ai/knowledge/act.py`（260 行） | 8 个 | 6 个核心过程 | 认知解离 + 接纳 + 价值澄清 + 承诺行动 + 观察自我 |
| **DBT**（辩证行为疗法） | `ai/knowledge/dbt.py`（396 行） | 4 个 | 4 个模块 | TIPP 危机降温 + 智慧心 + 激进接纳 + DEAR MAN 自信沟通 |
| **SFBT**（焦点解决短程疗法） | `ai/knowledge/sfbt_mi.py` | — | 4 个工具 | 奇迹问句 + 量尺问句 + 例外寻找 + 应对问句 |
| **MI**（动机访谈） | `ai/knowledge/sfbt_mi.py`（348 行） | — | OARS + 改变谈话 | 开放提问 + 肯定 + 反映 + 摘要 + 发展差距 |
| **基础知识** | `ai/knowledge/foundations.py`（134 行） | — | 12 篇专题文章 | 含动机冻结、自我批评、关系冲突、压力恢复等大众场景专题 |

**关键发现**：CBT 干预指南中有专门的 `procrastination` 条目，明确指出"Procrastination is not laziness or poor time management. It is primarily an emotion regulation problem."，与用户期望的轻度场景定位高度吻合。`foundations.py` 中的 `motivation_freeze` 条目解释了"冻结状态"的本质："What users call laziness is often a mix of depletion, fear of failure, perfectionism, overwhelm, and nervous-system shutdown."

### 5.3 意图识别与路由层面

源码的意图路由（`ai/routers/intent.py`）使用**关键词匹配**将用户消息分为 **6 种对话模式**，决定了后续整个处理链路的行为：

| 模式 | 触发关键词（部分） | 用途 | LLM 行为指引 |
|---|---|---|---|
| `support` | 默认 / 打招呼 / "你好" / 拒达关键词 | 情感支持、共情 | "Respond with warm emotional support, normalization, simple psychoeducation" |
| `intervention` | "练习""呼吸""放松""教我一个方法""缓解焦虑" | **触发练习工具** | "Respond with formulation-led self-help guidance only if the user clearly wants a technique" |
| `assessment` | "评估""量表""测试""筛查""PHQ""GAD" | 触发量表问卷 | "Respond with calm clarification, plain-language psychoeducation" |
| `planning` | "计划""下一步""安排""怎么做" | 行动计划 | "Respond with a low-pressure next step for daily stability" |
| `crisis` | "自杀""救救我""危机""want to die" | 危机干预 | "Respond with brief safety-first stabilization guidance" |
| `help` | "你能做什么""你是谁" | 功能说明 | 简要说明产品能力和边界 |

**拒达机制**：`REFUSAL_KEYWORDS` 检测到"不想做了""跳过""算了""不要"等关键词时，路由到 `support` 模式，退出当前干预或评估流程。

**关键发现**：意图路由已能识别"想做练习"这类意图（`INTERVENTION_KEYWORDS` 含"放松""呼吸""练习""带我做"等），用户说"想先做一点放松练习"会正确触发 `intervention` 模式。但当前所有路径都**必须经过对话流**（POST `/v1/conversations/respond`），没有"直接获取练习步骤开始做"的快捷入口。

### 5.4 会诊机制层面

源码的会诊系统（`ai/consultation.py`）实现了 **5 个流派的虚拟专家会诊**：

| 专家 | 流派 | 关注焦点 |
|---|---|---|
| CBT Agent | 认知行为疗法 | 自动思维、行为循环、维持因素、小实验 |
| Psychodynamic Agent | 精神动力学 | 核心冲突、依恋主题、重复关系模式、情感意义 |
| Humanistic Agent | 人本主义 | 感受体验、未满足需求、自我价值、共情理解 |
| ACT Agent | 接纳承诺疗法 | 接纳、解离、价值、心理灵活性 |
| DBT Agent | 辩证行为疗法 | 情绪调节、痛苦耐受、人际效能、安全 |

**触发条件**：当模式为 `intervention` 或 `assessment` 时自动触发；用户消息含"诊断""治疗""疗法""干预""会诊"等关键词也触发。high/critical 风险不触发会诊（先稳定化）。

**实现方式**：`infra/llm/generation.py` 的 `generate_multidisciplinary_consultation()` 使用 `ThreadPoolExecutor` 并行调用 5 个流派专家，每个专家独立生成内部意见（观察→形成→下一步），最后由 `build_consultation_synthesis_prompt()` 综合为一个用户可见回复。

### 5.5 结构化面试与干预过程层面

源码的 `ai/interview.py`（218 行）实现了 **7 个访谈阶段**和 **5 种提问策略**的动态切换：

| 阶段 | 提问策略 | 触发条件 | 质询许可 |
|---|---|---|---|
| `engagement` | open | 默认初始阶段 | 否 |
| `exploration` | open | "不知道""说不清""confused""overwhelmed" | 否 |
| `pattern_analysis` | looping | "总是""每次""every time""反复" | 否 |
| `hypothesis_testing` | clarifying / gentle_challenge | 检测到矛盾或绝对化 | **是** |
| `resistance_exploration` | gentle_challenge | 检测到回避或缩小化 | **是** |
| `formulation` | clarifying | intervention 模式 + 初始阶段 | 否 |
| `planning` | clarifying | planning 模式 | **是** |
| `structured_assessment` | clarifying | assessment 模式 | 否 |
| `safety_stabilization` | directive | crisis 模式 / high / critical 风险 | 否 |

**检测维度**（8 种语言特征）：开放探索、强模式、弱模式、矛盾、回避、绝对化、缩小化、耗竭。每种维度有中英文关键词列表。

**关键设计**：耗竭但无矛盾/绝对化/回避时**禁止质询**，先探索澄清耗竭来源（"Clarify whether the exhaustion is physical, emotional, relational, or anticipatory"）。

### 5.6 辅助功能层面

| 功能 | API | 源码位置 | 状态 |
|---|---|---|---|
| **评估量表** | `POST /v1/conversations/respond`（对话流拦截） | `domain/assessments/` | ✅ PHQ-9 / GAD-7 / ISI 完整流程 |
| **每日打卡** | `POST /v1/checkins` | `domain/checkins/` | ✅ mood / anxiety / sleep / energy 四维 |
| **趋势报告** | `GET /v1/reports/trends` | `domain/reports/trends.py` | ⚠️ 14 天均值比较，无异常检测 |
| **周报** | `GET /v1/reports/weekly` | `domain/reports/service.py` | ⚠️ 一行均值文本，无临床解读 |
| **练习工具 API** | `GET /v1/exercises` / `GET /v1/exercises/{tag}` | `api/routes/exercises.py` | ✅ 21 个练习，5 个分类 |
| **计划模板** | `GET /v1/plans` / `GET /v1/plans/{id}` | `domain/plans/` | ⚠️ 3 个静态模板，无每日内容 |
| **用户画像** | `GET /v1/users/{id}/profile` | `domain/users/` | ✅ concerns / goals / preferences / risk_notes |
| **会话历史** | `GET /v1/conversations/history` | `api/routes/conversation.py` | ✅ sessions / messages / risk-events |
| **危机安全** | 对话流内置 | `ai/safety/crisis.py` | ✅ 4 级风险 + 安全计划模板 + 手段限制 |
| **知识摄入** | CLI | `knowledge_ingestion.py`（852 行） | ✅ URL 抓取 + HTML 清洗 + PDF/TXT 导入 + 学习笔记合成 |

---

## 六、产品方向可行性评估

> 本章针对用户提出的三个产品方向，基于第五章的知识库覆盖分析结论，逐一评估可行性、现有基础、缺口和实现路径。

### 6.1 方向一：主打轻度心理问题覆盖大众场景

**用户原话**：「我觉得主打拖延，焦虑，不专注这种轻度的心理问题导致的各种工作，学习，亲密关系等大众场景实现起来用户覆盖更广。」

#### 可行性：极高 ✅，几乎是现成可用的

**知识覆盖已就绪**：

| 主题 | `TOPIC_KEYWORDS` 中的触发词 | 知识库支撑 |
|---|---|---|
| 拖延 | "拖延""逃避""开始不了""卡住了""procrastinating""avoid""stuck""can't start" | `cbt.py` 的 `procrastination` 干预指南 + `cost_benefit_analysis` 练习 + `behavioral_activation` 练习 |
| 焦虑 | "焦虑""紧张""心慌""担心""anxious""worry""fear""nervous" | `psychoeducation.py` 的 `anxiety_overview` + `cbt.py` 的 `anxiety_general` 干预指南 + `worry_tree` 练习 + `thought_record` 练习 |
| 不专注 | ⚠️ 未独立定义 | 部分被 `rumination` 主题覆盖（"脑子停不下来""钻牛角尖"），但缺少直接的专注力关键词 |
| 动力不足 | "没动力""提不起劲""停摆""冻结住了""unmotivated""lazy""freeze""shutdown" | `foundations.py` 的 `motivation_freeze` 条目 + `cbt.py` 的 `behavioral_activation` 练习 |
| 工作压力 | "压力""压得喘不过气""紧绷""stress""overloaded""tense" | `foundations.py` 的 `stress_recovery` 条目 + `psychoeducation.py` 的 `burnout_overview` |
| 亲密关系 | "关系""伴侣""吵架""边界""冲突""孤独""被孤立""relationship""partner""conflict" | `foundations.py` 的 `relationship_conflict` 条目 + `loneliness_and_isolation` 条目 + `dbt.py` 的 `dear_man_assertion` 练习 |

**产品定位已就绪**：`templates.py` 的角色提示明确写道"safety-first AI psychological support assistant for **mild-to-moderate** mental health needs"。`crisis.py` 中 `low` 级别的行为指引就是"Continue with standard support, psychoeducation, or intervention approach"。

**当前缺口**：

1. **"不专注 / 注意力不集中"主题缺失**：`TOPIC_KEYWORDS` 中无 `focus` 或 `concentration` 主题。当前部分被 `rumination` 覆盖，但 rumination 侧重"反复想""胡思乱想"，与"无法集中注意力""走神""分心"语义有差异
2. **场景触发词不够细化**：缺少"deadline""考试""加班""赶项目""复习""deadline""presentation"等具体场景触发词
3. **CBT 干预指南缺少"专注力"条目**：`CBT_INTERVENTION_GUIDES` 有 `anxiety_general`/`depression_general`/`sleep_hygiene`/`panic_attacks`/`rumination`/`anger`/`procrastination` 共 7 个条目，但没有专注力相关的干预指南

**建议改动量**：在 `ai/knowledge/index.py` 的 `TOPIC_KEYWORDS` 中增加 `focus` 主题（~30 分钟）；在 `cbt.py` 的 `CBT_INTERVENTION_GUIDES` 中增加 `focus_general` 条目（~1 小时）；在 `foundations.py` 中增加 `attention_difficulty` 条目（~1 小时）。总改动量极小。

#### 结论

| 维度 | 评估 |
|---|---|
| 知识覆盖 | ✅ 拖延、焦虑、动力不足、工作压力、亲密关系全部已覆盖，内容专业且详实 |
| 意图路由 | ✅ `INTERVENTION_KEYWORDS` 含"练习""放松""缓解焦虑"等触发词 |
| 会诊支持 | ✅ intervention 模式自动触发 5 流派会诊 |
| 需开发量 | 🔧 极小（补充 `focus` 主题关键词 + 1 个干预指南条目 + 1 篇基础文章，约 2-3 小时） |
| 优先级 | **P0** — 与产品定位完全一致，应作为核心场景 |

### 6.2 方向二：拒达＋意图识别配合个人专项练习方案生成个性化练习方案

**用户原话**：「群主的拒达＋意图识别，如果和个人专项练习配合起来生成个性化的练习方案场景就很明确了」

#### 可行性：高 ✅，但需要中等开发量来打通链条

**当前已有能力的完整清单**：

| 能力 | 现状 | 源码位置 |
|---|---|---|
| 意图识别 | ✅ 6 模式关键词路由，含 `intervention` 模式自动检测练习意图 | `ai/routers/intent.py` |
| 拒达机制 | ✅ `REFUSAL_KEYWORDS` 检测"不想做了""跳过""算了"等，路由回 `support` | `ai/routers/intent.py` |
| 练习库 | ✅ CBT(7) + ACT(8) + DBT(4) + 睡眠(1) + 惊恐(1) = 21 个结构化练习 | `ai/tools/exercises.py` |
| 主题检测 | ✅ `detect_topics()` 从消息中提取 Top-3 主题 | `ai/knowledge/index.py` |
| 知识-练习匹配 | ✅ `retrieve_knowledge_entries()` 按 topic+mode 检索，`exercise_topic_map` 将练习映射到主题 | `ai/knowledge/index.py` |
| 计划模板 | ⚠️ 3 个静态模板，无个性化 | `domain/plans/templates.py` |
| 会诊机制 | ✅ intervention 模式自动触发 5 流派会诊 | `ai/consultation.py` |
| 每日打卡 | ✅ mood/anxiety/sleep/energy 四维，有趋势分析 | `domain/checkins/` + `domain/reports/trends.py` |
| 评估结果 | ✅ PHQ-9/GAD-7/ISI 评分+严重度分级，有安全标志 | `domain/assessments/` |
| 用户画像 | ✅ concerns/goals/preferences/risk_notes | `domain/users/` |

**"个性化练习方案"的缺失环节**：

1. **个人画像的结构化不足**：当前 `memory_summary` 仅是对话文本摘要，`build_memory_snapshot()` 拼接快照但不结构化。用户画像 API 有 `preferences` 字段但无"练习偏好"子项。缺少"偏好呼吸类练习""对认知重构有阻抗""上次做想法记录反馈有效"等结构化练习偏好记录

2. **练习推荐逻辑未个性化**：当前 `intervention` 模式下，练习选择完全依赖 LLM 自行判断，`response_generator` 不注入"用户已做过哪些练习""哪些练习被拒达过""哪些主题与用户最相关"等上下文。`knowledge_loader` 虽然检索知识条目（含练习条目），但不区分"用户已完成的练习"和"未尝试的练习"

3. **计划模板是静态的**：`PLAN_TEMPLATES` 只有 `stabilization_7d`/`anxiety_14d`/`sleep_14d` 三个固定模板，每个模板仅有 `title`/`days`/`focus` 三个字段，无每日内容、无进度跟踪、无个性化生成逻辑

4. **拒达后的自适应不完整**：`REFUSAL_KEYWORDS` 检测到拒达后路由回 `support` 模式，但**不记录被拒达的练习类型**。`GraphState` 中无 `exercise_history` 或 `refusal_history` 字段。下次可能再次推荐同类练习，形成循环

5. **打卡与推荐未打通**：`checkins` 数据有趋势分析（`trends.py`），但趋势结果不反哺推荐逻辑。如连续 3 天 anxiety_score > 7 应自动推荐焦虑相关练习，但当前无此机制

**实现路径（按优先级分阶段）**：

| 阶段 | 改动 | 文件 | 工作量 |
|---|---|---|---|
| **短期** | `GraphState` 增加 `exercise_history` / `refusal_history` 字段；`route_intent` 检测拒达时记录被拒达的练习类型；`response_generator` 注入"避免推荐已拒达练习"提示 | `schemas/state.py` + `routers/intent.py` + `nodes/response_generator.py` | 2-3 天 |
| **中期** | `domain/plans/service.py` 增加 `generate_personalized_plan()` 方法，结合 checkins 趋势 + assessment 分数 + topics 检测生成动态计划；用户画像 `preferences` 增加练习偏好子项 | `domain/plans/service.py` + `domain/users/schemas.py` | 1-2 周 |
| **长期** | 引入用户画像持久化表，记录练习完成率、偏好类型、拒达模式；实现基于协同过滤或规则引擎的推荐算法 | `infra/db/models.py` + 新增 `domain/recommendations/` | 2-3 周 |

#### 结论

| 维度 | 评估 |
|---|---|
| 意图识别 + 拒达 | ✅ 已有完整的关键词检测 |
| 练习库 | ✅ 21 个练习，5 个分类 |
| 知识-练习匹配 | ✅ `exercise_topic_map` 已有主题映射 |
| 个性化推荐 | ❌ 缺失——无结构化练习偏好、无拒达历史记录、无趋势反哺 |
| 计划个性化 | ❌ 缺失——3 个静态模板 |
| 需开发量 | 🔧 中等（短期 2-3 天打通拒达记忆链路，中期 1-2 周实现动态计划，长期 2-3 周实现推荐算法） |
| 优先级 | **P1** — 作为产品核心价值闭环的关键环节 |

### 6.3 方向三：增加"练习工具"入口

**用户原话**：「我觉得可以增加"练习工具"入口。比如用户截图里说"想先做一点放松练习"。这类意图其实很适合不走长对话，直接给工具：呼吸练习、渐进式肌肉放松、睡前卸载清单、情绪记录。可以在前端做成几个按钮：呼吸、睡眠、焦虑、记录一下。」

#### 可行性：极高 ✅，后端已就绪，前端改动极小

**后端已有能力**：

```python
# api/routes/exercises.py 已有两个端点：
GET  /v1/exercises              → 返回所有练习分类和标签
GET  /v1/exercises/{exercise_tag} → 返回具体练习的步骤和引导语
```

```python
# ai/tools/exercises.py 的 list_all_exercises() 返回：
{
    "cbt":   ["behavioral_activation", "thought_record", ...],          # 7 个
    "act":   ["defusion", "values_clari", ...],                          # 8 个
    "dbt":   ["tipp", "wise_mind", ...],                                 # 4 个
    "sleep": ["wind_down"],                                              # 1 个
    "panic": ["grounding_5_4_3_2_1"]                                     # 1 个
}
```

**前端已有先例**：`static/index.html` 已有 9 个 `quick-actions` 按钮，包括"教我做呼吸练习"按钮。但当前所有按钮只是发送一条消息给对话 API（`POST /v1/conversations/respond`），走完整对话流，不是直接调取练习。

**用户提出的四个按钮与现有练习的映射**：

| 用户期望按钮 | 最佳映射练习 tag | 练习内容 | 所在文件 |
|---|---|---|---|
| **呼吸** | `dbt_tipp` | TIPP 技能含 Paced Breathing（4 拍吸 / 8 拍呼，5 次）+ 渐进式肌肉放松 | `tools/exercises.py` |
| **睡眠** | `sleep_wind_down` | 睡前卸载清单：设截止时间→调暗灯光→选一项放松活动→保持卧室凉爽无钟→杂念写记事本 | `tools/exercises.py` |
| **焦虑** | `cbt_worry_tree` 或 `cbt_thought_record` | 担忧树：分流可解决/不可解决的担忧；想法记录：7 步结构化认知重构 | `knowledge/cbt.py` |
| **记录一下** | `cbt_thought_record`（简化版） | 情绪记录：情境→情绪+强度→自动思维 | `knowledge/cbt.py` |

**当前缺口**：

1. **缺少独立的"快速练习"入口路径**：当前练习必须通过对话流（POST `/v1/conversations/respond`）触发，用户说"想先做一点放松练习"时，`INTERVENTION_KEYWORDS` 检测到"放松"会路由到 `intervention` 模式，但后续仍由 LLM 生成回复（含练习引导），不是直接返回练习步骤 JSON 供前端渲染
2. **前端缺少练习工具面板**：`index.html` 只有聊天界面和快捷消息按钮，无独立的练习面板 UI
3. **练习是"步骤列表"而非"交互式引导"**：`get_exercise_by_tag` 返回一个 JSON 对象（`steps` 数组 + `output_format` 字符串），没有练习进度追踪、步骤交互的独立会话管理
4. **"情绪记录"无独立简化入口**：当前的情绪记录必须走完整的 CBT 想法记录 7 步流程，没有"快速记录"（情绪+强度+触发情境三字段）的简化版本

**实现方案**：

| 方案 | 改动内容 | 工作量 | 适用场景 |
|---|---|---|---|
| **方案 A（最小改动）** | 前端增加练习工具面板，按钮直接调 `GET /v1/exercises/{tag}` 获取步骤，用 JS 实现步骤式展示（一步一步显示，用户点"下一步"推进），不经过对话流 | 1-2 天 | MVP 快速上线 |
| **方案 B（中等改动）** | 增加 `POST /v1/exercises/{tag}/start` API，创建独立练习会话，记录进度到 DB，完成后更新用户画像。对话中检测到"想做练习"意图时也可跳转到练习面板 | 3-5 天 | 练习进度追踪 |
| **方案 C（完整方案）** | 在方案 B 基础上增加"情绪记录"独立功能（简化版：情绪+强度+触发情境三字段快速记录），记录数据写入 `checkins` 表或新建 `emotion_logs` 表，用于趋势分析和个性化推荐 | 1-2 周 | 完整体验闭环 |

#### 结论

| 维度 | 评估 |
|---|---|
| 后端 API | ✅ `GET /v1/exercises` + `GET /v1/exercises/{tag}` 已存在 |
| 练习库 | ✅ 21 个练习，5 个分类，内容专业完整 |
| 前端面板 | ❌ 缺失——只有聊天快捷按钮，无独立练习 UI |
| 交互式引导 | ❌ 缺失——练习是 JSON 步骤列表，无进度追踪 |
| 需开发量 | 🔧 极小（方案 A：1-2 天前端面板；方案 B：3-5 天含 API；方案 C：1-2 周含情绪记录） |
| 优先级 | **P0** — 与方向一并行推进，立即提升产品可用性 |

### 6.4 三方向协同关系与综合结论

三个方向之间存在天然的协同关系，构成完整的产品价值链：

```
用户进入 → 练习工具入口（方向三）→ 做练习 / 记录情绪（不走长对话）
                                    ↓
                               对话流 → 意图识别 + 拒达（方向二）→ 个性化推荐练习
                                    ↓
                               知识库覆盖轻度场景（方向一）→ 拖延 / 焦虑 / 不专注 / 关系问题
```

| 方向 | 可行性 | 现有基础 | 需开发量 | 优先级 |
|---|---|---|---|---|
| **方向一：轻度问题覆盖** | 极高 | 知识库已完整覆盖拖延/焦虑/动机/关系 | 极小（补充 `focus` 主题关键词，~2-3 小时） | **P0** |
| **方向二：个性化练习方案** | 高 | 有意图识别 + 拒达 + 练习库 + 会诊，但缺画像持久化和推荐逻辑 | 中等（短期 2-3 天，中期 1-2 周，长期 2-3 周） | **P1** |
| **方向三：练习工具入口** | 极高 | API 已存在，前端有按钮先例 | 极小（方案 A：1-2 天前端面板） | **P0** |

**建议执行顺序**：方向一和方向三可**立即并行推进**（一个补关键词、一个加前端面板），方向二作为中期演进方向，需要先通过方向三积累用户练习数据后再实现推荐逻辑。

---

## 七、问题分类与优先级

### 🔴 P0 — 上线必须完善（安全与数据完整性硬门槛）

| # | 问题 | 源码位置 | 风险说明 |
|---|---|---|---|
| 1 | **`response_generator.py` fallback bug** | 第 57-60 行：`raise RuntimeError` 后 `state["fallback_used"] = True` 不可达 | LLM 调用失败时服务直接崩溃，用户无任何回复 |
| 2 | **Alembic 迁移不完整** | `questionnaire_sessions` 表无迁移；`assessments` 表缺 5 个字段（`plain_meaning`/`functional_impact`/`care_consideration`/`disclaimer`/`needs_safety_followup`） | PostgreSQL 生产环境跑 Alembic 后问卷功能崩溃 |
| 3 | **safety_reviewer 空心化 + 一刀切替换** | `ai/nodes/safety_reviewer.py` 仅检测 prompt 泄露，且检测到后整条回复被全量替换为固定兜底句 | 不检查回复是否含诊断语言、不当建议、越界承诺；"Respond with" 等极短标记易误匹配正常文本，导致好回复被误杀 |
| 4 | **.env 密钥泄露风险** | `.env` 含明文 API Key 和 Langfuse 密钥 | 如果 git 提交会导致密钥泄露 |
| 5 | **无诊断请求确定性拦截** | `ai/routers/intent.py` 无诊断请求关键词 | 用户问"我是不是抑郁症"时 LLM 可能给出诊断性回复，违反安全边界。详见[质询机制分析 §4.6](#46-拒答机制现状) |
| 6 | **High 风险回复硬编码不走 LLM** | `ai/safety/crisis.py` `build_crisis_reply()` + `ai/nodes/response_generator.py` 第 17-20 行 | high 级别的被动自杀意念（如"想死""不想活了"）与 critical 级别的主动计划/已付诸行动得到**完全相同的固定模板回复**，回复开头即"我很担心你的安全。建议你立即拨打120急救"，对 passive suicidal ideation 过于生硬。high 级别应仍走 LLM 生成共情回复 + 注入危机行为指导，仅 critical 保留纯模板 |
| 7 | **意图路由与风险分类关键词冲突** | `ai/routers/intent.py` `CRISIS_KEYWORDS` vs `ai/safety/rules.py` `HIGH_RISK_KEYWORDS` | 两套独立关键词体系存在重叠和冲突，"help me" 在 intent.py 中被归为危机关键词（过于宽泛）但 rules.py 不认为高风险。risk_classifier 先跑可能设 mode=crisis 导致 intent_router 被跳过，或反过来两者对 crisis 判断不一致 |

### 🟡 P1 — 应该做（影响产品可用性和核心价值闭环）

| # | 问题 | 说明 |
|---|---|---|
| 8 | **前端功能补全** | 后端评估/练习/打卡 API 已就绪，但前端未暴露，用户只能聊天。含练习工具面板（呼吸/睡眠/焦虑/记录按钮 → 直接调取 `GET /v1/exercises/{tag}`，详见[方向三可行性评估 §6.3](#63-方向三增加练习工具入口)） |
| 9 | **跨轮次矛盾检测** | 当前质询仅限单轮，无法发现用户前后表述矛盾。这是质询机制的核心缺口，详见[质询机制分析 §4.7](#47-核心缺口) |
| 10 | **干预计划内容填充** | 3 个计划模板仅存目录，无每日步骤和进度跟踪 |
| 11 | **质询合理性后置审查** | safety_reviewer 应检查 LLM 质询是否过度激进、是否在不应质询时质询了。详见[质询机制分析 §4.7](#47-核心缺口) |
| 12 | **周报临床化** | 仅均值统计无异常检测，不具备早期预警能力 |
| 13 | **评估体系深化** | 补充躁狂/OCD/焦虑+失眠共病场景，增加回复质量评估维度 |
| 14 | **跨轮次风险追踪** | `risk_classifier` 仅看当前消息，不读取对话历史。连续两轮 elevated 以上应自动升级，但当前无此机制。详见[质询机制分析 §4.7](#47-核心缺口) |
| 15 | **否定检测语义化** | 当前否定检测为布尔子串匹配，无法处理双重否定、时态区分、"曾经想过"等场景。详见[质询机制分析 §4.7](#47-核心缺口) |
| 16 | **Temperature 调参** | `factory.py` 设 `temperature=0.0` 导致回复同质化，建议按模式分温度：crisis 用 0.0（稳定），support 用 0.4-0.6（自然） |
| 17 | **"不专注"主题补全** | `TOPIC_KEYWORDS` 中缺少 `focus` 主题，需增加"无法集中""走神""分心""concentration""distracted"等触发词 + CBT 干预指南 `focus_general` 条目。详见[方向一可行性评估 §6.1](#61-方向一主打轻度心理问题覆盖大众场景) |

### 🟢 P2 — 可以做（增强能力，不阻塞上线）

| # | 方向 | 说明 |
|---|---|---|
| 15 | **pgvector 语义检索** | 当前关键词检索在非安全路径已够用，语义检索可提升匹配质量但不能作为安全门槛 |
| 16 | **LLM 模型路由** | 简单支持用轻量模型，复杂会诊用强模型，降低成本和延迟 |
| 17 | **LLM 降级策略** | 主模型不可用时 fallback 到模板回复，保证服务连续 |
| 18 | **知识库中文化** | 当前摄入源全为英文 NIMH/MedlinePlus，补充中文权威来源 |
| 19 | **Redis/Celery 集成** | 异步摘要写入、定时周报生成 |
| 20 | **耗竭检测精细化** | 区分身体疲劳/情绪耗竭/关系疲惫/预期性焦虑，提升质询精度。详见[质询机制分析 §4.7](#47-核心缺口) |
| 21 | **意图/风险关键词体系统一** | 统一 `intent.py` 和 `rules.py` 的关键词体系，消除重叠和冲突，见 [P0 #7](#-p0--上线必须完善安全与数据完整性硬门槛) |
| 22 | **个性化练习推荐** | 打通拒达记忆链路（`GraphState` 增加 `exercise_history`/`refusal_history`）+ 趋势反哺推荐逻辑 + 动态计划生成。详见[方向二可行性评估 §6.2](#62-方向二拒达意图识别配合个人专项练习方案) |

### 🔵 P3 — 前瞻方向（未来演进）

| # | 方向 | 说明 |
|---|---|---|
| 23 | **Langfuse Score 评估闭环** | 在 Langfuse 中对每次对话做人工/AI 评分，形成"trace → score → 优化"闭环 |
| 24 | **Relapse 预警** | 基于 check-in 趋势 + 评估历史，预测复发风险并主动干预 |
| 25 | **个性化策略路由** | 根据用户画像和历次效果数据，动态选择最优干预流派 |
| 26 | **单轮关键词检测精度提升** | 引入 LLM 辅助的语用分析，区分"但是"是真正转折还是修辞。详见[质询机制分析 §4.7](#47-核心缺口) |
| 27 | **多语言扩展** | 当前仅中英双语，可扩展日韩等 |
| 28 | **Admin 后台** | 风险事件审计、内容质量审查、用户管理 |
| 29 | **完整推荐算法** | 基于协同过滤或规则引擎的个性化练习推荐，需先积累用户练习数据。详见[方向二可行性评估 §6.2](#62-方向二拒达意图识别配合个人专项练习方案) |

---

## 八、下一步建议执行顺序

```
立即修复（P0）
  ├── 1. 修复 response_generator.py fallback bug
  ├── 2. 补全 Alembic 迁移（questionnaire_sessions + assessments 字段）
  ├── 3. 增强 safety_reviewer（诊断语言检测 + 边界检查 + 一刀切替换改为截断清洗）
  ├── 4. .env 加入 .gitignore，提供 .env.example
  ├── 5. 诊断请求确定性拦截路由（intent.py 增加诊断关键词检测）
  ├── 6. High 风险回复改走 LLM（注入危机行为指导 prompt，仅 critical 保留纯模板）
  └── 7. 统一 intent.py 与 rules.py 关键词体系，消除重叠冲突

短期补足（P1）
  ├── 8.  前端：评估问卷引导界面 + 每日打卡界面 + 练习工具面板（呼吸/睡眠/焦虑/记录按钮）
  ├── 9.  跨轮次矛盾检测节点（consultation_planner 引入历史消息对比）
  ├── 10. 干预计划每日内容填充
  ├── 11. 质询合理性后置审查（safety_reviewer 增加质询强度检查）
  ├── 12. 周报异常检测逻辑
  ├── 13. 评估场景补全 + 回复质量评估
  ├── 14. 跨轮次风险追踪（risk_classifier 读取 memory_summary，连续 elevated 自动升级）
  ├── 15. 否定检测语义化（引入窗口距离 / LLM 二次确认）
  ├── 16. Temperature 按模式分温（crisis=0.0, support=0.4-0.6）
  └── 17. "不专注"主题补全（TOPIC_KEYWORDS 增加 focus 主题 + CBT 干预指南 focus_general 条目）
           └── 与方向三练习工具面板并行推进，覆盖拖延/焦虑/不专注/关系问题

中期增强（P2）
  ├── 18. pgvector 语义检索（非安全路径）
  ├── 19. 模型路由 + 降级策略
  ├── 20. 知识库中文源补充
  ├── 21. Redis/Celery 异步任务
  ├── 22. 耗竭检测精细化
  ├── 23. 意图/风险关键词体系统一（如 P0 未完成则在此补齐）
  └── 24. 个性化练习推荐（拒达记忆链路 + 趋势反哺 + 动态计划生成）
           └── 依赖方向三积累用户练习数据后推进

长期演进（P3）
  ├── 25. Langfuse Score 闭环
  ├── 26. Relapse 预警模型
  ├── 27. 个性化策略路由
  ├── 28. 单轮关键词检测精度提升（LLM 辅助语用分析）
  ├── 29. 多语言扩展
  ├── 30. Admin 审计后台
  └── 31. 完整推荐算法（协同过滤 / 规则引擎）
```

---

*本报告基于全量源码审查生成，如需对某个具体问题展开方案设计，请单独提出。*
