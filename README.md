# psych-support-bot

AI 心理支持机器人后端：以安全为第一优先级的对话服务，提供情绪支持、量表测评、每日打卡、干预计划与周报，配套经用户同意和本地去标识化的 Langfuse 产品分析与 LLM-as-judge 离线评测。

> ⚠️ 免责声明：本项目用于心理支持与心理教育场景，**不构成医疗诊断或治疗建议**。检测到危机风险时，系统会拦截常规对话并引导求助专业资源。

## 核心能力

- **安全优先的对话流**（LangGraph 编排）：风险分类器前置（low / elevated / high / critical 四级 + 危机关键词拦截），意图路由（support / assessment / intervention / planning / crisis），生成后安全审查（剥离对抗性表述、按允许范围约束挑战式语言）
- **多学派咨询规划**：CBT、精神动力、人本主义、ACT、DBT、SFBT/MI 的知识模块与联合会诊式规划
- **跨轮语义增强**：跨轮矛盾检测、风险追踪、耗竭子类型识别、否定邻近匹配
- **交互式练习库**：分步练习引导，拒绝跟随跟踪
- **结构化测评**：PHQ-9 / GAD-7 / ISI 量表与严重度分级；每日打卡（每天一次，锁定只读回看）
- **干预计划与周报**：计划模板 + 每日内容下发、周期报告与趋势分析
- **知识库摄取**：`psych-support-bot-ingest-knowledge` 入口，本地/公开语料入库（`data/knowledge/` 已含 NIMH 等预处理语料）
- **可观测性与评测**：Langfuse 记录去标识化的本轮用户输入、最终展示回复和运行指标；安全回归数据集（`tests/evals/`）；LLM-as-judge 评测层

## 技术栈

Python 3.12 · FastAPI · LangGraph / LangChain · SQLAlchemy 2 + Alembic · SQLite（开发）/ PostgreSQL（生产，依赖已备 pgvector）· Celery（脚手架）· Langfuse · uv · ruff

## 目录速览

```
src/psych_support_bot/
├── ai/            # LangGraph 工作流：nodes/ 节点、safety/ 规则、knowledge/ 学派知识、prompts/
├── api/routes/    # REST：conversation, assessments, checkins, plans, reports, exercises…
├── domain/        # 领域服务：assessments, checkins, plans, reports, users
├── evals/         # 离线评测 runner 与 LLM-as-judge
├── infra/         # config / db / llm / telemetry(Langfuse)
├── services/      # 应用服务
└── static/        # 内置前端单页（index.html）
data/knowledge/    # 知识库语料与摄取产物
docs/history/      # 各阶段规划文档存档（PROJECT_PLAN / PROGRESS / ANALYSIS）
migrations/        # Alembic 迁移
tests/{unit,integration,evals}/
```

## 快速开始

要求 Python ≥ 3.12 与 [uv](https://docs.astral.sh/uv/)。

```bash
make setup          # 安装依赖 + pre-commit + 复制 .env + 迁移数据库
# 编辑 .env 填入 LLM API Key
make serve          # 启动 http://127.0.0.1:8000 （内置前端开箱即用）
```

Windows 下无需命令行：双击 `一键启动.bat` 即可，详见《使用说明-小白版.md》。

### 配置（.env）

| 变量组 | 说明 |
| --- | --- |
| `OPENAI_API_KEY` / `OPENAI_BASE_URL` / `OPENAI_MODEL` | LLM 接口（OpenAI 兼容协议） |
| `DATABASE_URL` | 默认 `sqlite:///./data/psych_support_bot.db`，生产可换 PostgreSQL |
| `LANGFUSE_*` | 产品分析上报、稳定假名密钥与注销清理（可选） |
| `JUDGE_API_KEY` / `JUDGE_BASE_URL` / `JUDGE_MODEL` | LLM-as-judge 评测裁判模型 |
| `AUTH_*` | 内部账户、短期 Access Token、轮换会话、OIDC client ID 允许列表与未来 Passkey RP 配置 |

完整项见 `.env.example`。

### 账户与多端身份基础

- 新账户使用随机 `account_id` 作为全部业务数据的唯一归属键；用户名只是登录句柄，旧账户 ID 保持不变。
- Access Token 默认 15 分钟，只保存在页面内存；Web Refresh Token 只进入 HttpOnly、SameSite Cookie，原生端通过显式 `native` 会话传输交给 Keychain/Keystore；每次刷新立即轮换并阻止重放。
- `/v1/auth/oidc/challenge` 和 `/v1/auth/oidc/exchange` 提供 Apple、Google、Huawei 共用的服务端验证边界。对应 `AUTH_*_CLIENT_IDS` 为空时供应商保持关闭。
- OIDC 只根据服务端固定的 issuer、JWKS 与 client ID 允许列表校验，账户映射使用加密密钥生成的 `(provider, issuer, subject)` 假名，不按邮箱自动合并。
- Passkey 数据表已预留，但 `AUTH_PASSKEY_RP_ID` 和精确 Origins 未配置前不开放注册，避免把凭据绑定到临时域名。

设计、验收条件和后续平台接入任务见 [Identity Foundation](specs/identity-foundation/design.md)。

### 个性化记忆系统

系统从对话中逐步形成对用户的个性化理解，用于调整回复方式、选择更合适的知识和干预方法，不用于诊断或人格推断。

**提取层（同步 K1 + 异步 K2/K3）：**
- K1 确定性提取（每轮）：D1 话题、D4 练习效果、D5 目标、D7 保护因素、生活事件、身份背景（11 维度）
- K2 LLM 语义提取（异步 Worker）：D2 严重度、D3 机制+维持循环、D5 改变谈话梯度、D6 表达偏好
- K3 综合引擎（异步 Worker）：从碎片信念中生长结构性理解 + 支持策略编译

**接出层（驱动 Agent 行为）：**
- D6 表达偏好 → 语气指令注入 turn context
- D5 MI 梯度 → 改变连续谱位置摘要
- D3 机制 → 学派知识匹配（ACT/CBT/DBT 优先）
- D2 严重度 → 干预强度调整（不告知用户）
- 画像快照 → 支持策略注入（response_length/pacing/knowledge_paths）

**安全与用户控制：**
- 画像开关：用户可随时暂停/恢复，暂停后所有画像数据停止使用
- 清除画像：级联删除信念/事件/统计/任务/快照/背景/理解
- 敏感信息：医疗/宗教/创伤/家族史/物质使用不做长期保存
- 面板只展示用户主动提供的事实，不展示推断性内容
- 异步 Worker 支持多实例部署、原子抢占、崩溃恢复

设计文档见 [PROFILE_DECISIONS.md](docs/technical/PROFILE_DECISIONS.md) 和 [Agent 侧画像演进计划](docs/plans/2026-09-15-agent-facing-profile-evolution.md)。

### 测试与质量

```bash
make test           # 全量（unit + integration + evals）
make lint           # ruff 检查 + 格式校验
uv run psych-support-bot-evals   # 安全回归评测数据集
uv run psych-support-bot-judge   # LLM-as-judge 评测
```

CI（GitHub Actions）：lint → 单元测试（SQLite）→ evals（有 Key 时）→ 密钥泄露扫描。

### 数据库迁移

```bash
make migrate                    # 升级到最新
make migrate-new m="描述"       # 依据 ORM 模型变更自动生成迁移
```

## 部署（Linux 服务器，Docker）

```bash
bash deploy.sh              # 构建 Dockerfile.server 镜像并启动容器栈
./stop.sh && ./start.sh     # 停止 / 启动
```

- 栈定义：`Dockerfile.server` + `docker-compose.server.yml`（宿主端口映射见该文件，当前 `9958 → 容器 8000`；数据与日志落持久卷）
- 分步说明、防火墙放行、systemd 开机自启等见《服务器部署说明.md》

## 隐私、数据流与注销边界

提交内容或使用 AI 前，用户必须同意当前版本的数据处理协议；协议版本或实际供应商变化会触发重新同意。拒绝或撤回后仍可查看、导出和注销，但不能继续提交内容或调用 AI。

- **业务数据库**保存账号、对话、摘要、测评、练习、打卡、计划、周报、个性化记忆、切片和动作元数据。导出覆盖同一用户数据清单，密码哈希除外。
- **个性化记忆由用户控制**：“我”页可暂停记忆提取与使用，也可经二次确认关闭并删除记忆数据（包括信念、事件、统计、任务、快照、背景和理解）；安全风险识别始终独立运行。
- **模型与语音供应商**只接收完成功能所需内容。文本模型可能收到本轮输入、相关历史、摘要和个性化记忆；语音识别接收音频，语音合成接收待朗读文本。应用不持久保存录音。
- **Langfuse 产品分析**只在配置启用且用户同意后接收本轮用户输入、最终展示回复和运行指标。常见联系方式、证件号、IP、链接和凭据在本地替换，原始用户/会话 ID 改为密钥生成的不可逆假名。系统提示词、历史上下文、摘要和个性化记忆字段不上传。自由文本仍可能通过姓名或事件被重新识别，因此不能承诺绝对匿名。
- **商业化用量埋点**`usage_events` 只记录动作类型、时间和来源，不记录情绪内容。
- **注销**同步删除在线业务数据库和当前浏览器中的产品缓存，并异步删除 Langfuse 中可关联的新旧追踪。用户可用随机回执查询清理状态。下载文件、其他设备缓存、离线备份和模型供应商留存不属于页面可即时证明已删除的范围。

完整字段、目的地与删除责任见 [数据生命周期说明](docs/technical/PRIVACY_DATA_LIFECYCLE.md)。生产环境必须保持 `LANGFUSE_PSEUDONYM_KEY` 稳定，并建立备份到期删除及恢复前重放注销清单的运维流程。

## 协作方式

云端 `main` 由 redmaplewww 维护并通过 PR 收口；协作侧在 fork 上按阶段分支迭代（如 `pr1/langfuse-collab-infra`、`pr3/p1-*`），完成后 PR 合入。贡献细节见 [CONTRIBUTING.md](CONTRIBUTING.md)。
