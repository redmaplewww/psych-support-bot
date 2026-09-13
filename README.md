# psych-support-bot

AI 心理支持机器人后端：以安全为第一优先级的对话服务，提供情绪支持、量表测评、每日打卡、干预计划与周报，配套 Langfuse 全链路追踪与 LLM-as-judge 离线评测。

> ⚠️ 免责声明：本项目用于心理支持与心理教育场景，**不构成医疗诊断或治疗建议**。检测到危机风险时，系统会拦截常规对话并引导求助专业资源。

## 核心能力

- **安全优先的对话流**（LangGraph 编排）：风险分类器前置（low / elevated / high / critical 四级 + 危机关键词拦截），意图路由（support / assessment / intervention / planning / crisis），生成后安全审查（剥离对抗性表述、按允许范围约束挑战式语言）
- **多学派咨询规划**：CBT、精神动力、人本主义、ACT、DBT、SFBT/MI 的知识模块与联合会诊式规划
- **跨轮语义增强**：跨轮矛盾检测、风险追踪、耗竭子类型识别、否定邻近匹配
- **交互式练习库**：分步练习引导，拒绝跟随跟踪
- **结构化测评**：PHQ-9 / GAD-7 / ISI 量表与严重度分级；每日打卡（每天一次，锁定只读回看）
- **干预计划与周报**：计划模板 + 每日内容下发、周期报告与趋势分析
- **知识库摄取**：`psych-support-bot-ingest-knowledge` 入口，本地/公开语料入库（`data/knowledge/` 已含 NIMH 等预处理语料）
- **可观测性与评测**：Langfuse trace_span 全链路追踪；安全回归数据集（`tests/evals/`）；LLM-as-judge 评测层

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
| `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` / `LANGFUSE_HOST` | 追踪上报（可选） |
| `JUDGE_API_KEY` / `JUDGE_BASE_URL` / `JUDGE_MODEL` | LLM-as-judge 评测裁判模型 |

完整项见 `.env.example`。

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

## 隐私与数据边界（商业化埋点的伦理约束）

记录与洞察功能线（测评历史 / 打卡趋势 / 练习记录）遵循以下硬边界：

- **埋点只记动作元数据**：`usage_events` 表只写事件类型（`exercise_completed` / `assessment_submitted` / `checkin_created` / `ai_analysis_requested` / `ai_analysis_served`）、时间与来源渠道，用于将来商业化配额计量。
- **情绪内容不进埋点、不做画像**：mood 分数、打卡备注、测评分数与 band、练习反思笔记，一律不进入埋点表，也不进入任何商业化分析。
- **AI 解读的最小输入**：三个 `/analysis` 端点喂给 LLM 的只有数值序列与动作元数据；打卡备注与练习反思的文字内容不进 prompt。
- **记录查看永久免费**：付费锚点只设在 AI 解读（`/analysis` 独立端点，将来挂配额中间件），记录本身是留存抓手，不设墙。

## 协作方式

云端 `main` 由 redmaplewww 维护并通过 PR 收口；协作侧在 fork 上按阶段分支迭代（如 `pr1/langfuse-collab-infra`、`pr3/p1-*`），完成后 PR 合入。贡献细节见 [CONTRIBUTING.md](CONTRIBUTING.md)。
