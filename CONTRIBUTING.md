# 贡献指南

## 快速开始

```bash
# 1. Clone 仓库
git clone https://github.com/redmaplewww/psych-support-bot.git
cd psych-support-bot

# 2. 安装依赖（需要 Python 3.12+）
uv sync

# 3. 复制配置文件并填入密钥
cp .env.example .env
# 编辑 .env 填入 OPENAI_API_KEY 和 Langfuse 密钥

# 4. 初始化数据库
uv run alembic upgrade head

# 5. 启动服务
uv run uvicorn psych_support_bot.app:app --host 0.0.0.0 --port 8000 --reload

# 6. 运行测试
uv run pytest
```

## 分支策略

```
main          ← 稳定分支，只接受 PR，不直接 push
  ├── dev     ← 开发集成分支，PR 合入此处
  ├── feat/*  ← 功能分支，如 feat/langfuse-tracing
  ├── fix/*   ← 修复分支，如 fix/response-fallback
  └── chore/* ← 杂项分支，如 chore/update-deps
```

### 规则

- **`main` 分支受保护**：只能通过 PR 合入，需要至少 1 个 review
- **`dev` 分支**：日常开发的集成分支，功能分支 PR 指向 `dev`
- **功能分支命名**：`feat/描述` / `fix/描述` / `chore/描述`，全小写连字符
- **分支生命周期**：合入后删除，保持分支列表干净

### 工作流示例

```bash
# 从 dev 拉功能分支
git checkout dev
git pull origin dev
git checkout -b feat/cross-turn-contradiction

# 开发完成后提交
git add .
git commit -m "feat: add cross-turn contradiction detection node"

# 推送并创建 PR → dev
git push origin feat/cross-turn-contradiction
# 在 GitHub 上创建 PR: feat/cross-turn-contradiction → dev
```

## 提交规范

使用 [Conventional Commits](https://www.conventionalcommits.org/) 格式：

```
<type>(<scope>): <subject>

<body 可选>

<footer 可选>
```

### Type 列表

| type | 用途 | 示例 |
|---|---|---|
| `feat` | 新功能 | `feat(safety): add diagnosis request interception` |
| `fix` | 修复 bug | `fix(response): unreachable fallback code after raise` |
| `refactor` | 重构（不改行为） | `refactor(tracing): extract trace_span as context manager` |
| `docs` | 文档 | `docs: add project analysis report` |
| `test` | 测试 | `test(safety): add mania and OCD eval cases` |
| `chore` | 构建/依赖/配置 | `chore: add pre-commit hooks` |
| `ci` | CI 配置 | `ci: add GitHub Actions test workflow` |

### Scope 建议

```
ai          ← AI 工作流、节点、Prompt
safety      ← 风险分类、危机拦截、安全审查
assessments ← 量表评估
tracing     ← Langfuse、可观测性
db          ← 数据库、迁移、ORM
api         ← FastAPI 路由
frontend    ← 前端界面
```

## 代码质量

### Pre-commit（自动检查）

```bash
# 安装 pre-commit
uv tool install pre-commit

# 在项目根目录执行（会自动安装 git hooks）
pre-commit install

# 之后每次 git commit 会自动运行：
# - trailing-whitespace / end-of-file-fixer（格式）
# - ruff --fix + ruff-format（代码风格）
# - detect-secrets（密钥泄露检测）
```

如需跳过（紧急情况）：`git commit --no-verify`（不建议常态使用）

### 测试

```bash
# 全量测试
uv run pytest

# 仅单元测试
uv run pytest tests/unit/

# 仅集成测试
uv run pytest tests/integration/

# 仅安全评估
uv run pytest tests/evals/

# 查看详细输出
uv run pytest -v

# 指定文件
uv run pytest tests/unit/test_interview.py -v
```

### 数据库迁移

```bash
# 修改 ORM model 后生成新迁移
uv run alembic revision --autogenerate -m "description of changes"

# 执行迁移
uv run alembic upgrade head

# 回滚一个版本
uv run alembic downgrade -1
```

## 环境要求

- Python ≥ 3.12
- uv（包管理器）
- 本地开发默认 SQLite，无需额外数据库
- 生产环境需要 PostgreSQL 14+
- Langfuse 密钥仅在需要 trace 时配置（不配置不会报错，自动降级为 no-op）

## 代码评审标准

- [ ] 通过 pre-commit 检查
- [ ] 通过全部测试
- [ ] 新增功能有对应测试
- [ ] 涉及安全的改动有 eval case 覆盖
- [ ] 不提交 `.env` 文件
- [ ] 数据库 schema 变更包含 Alembic 迁移
- [ ] PR 描述清晰，说明改了什么、为什么改
