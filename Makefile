.PHONY: setup dev test test-unit test-integration test-eval lint format migrate migrate-new db-reset serve deploy clean help

PY := uv run
PORT ?= 8000

help: ## 显示所有可用命令
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

setup: ## 首次安装：依赖 + pre-commit + 数据库
	$(PY) sync
	@command -v pre-commit >/dev/null 2>&1 || uv tool install pre-commit
	pre-commit install
	cp -n .env.example .env 2>/dev/null || true
	$(PY) alembic upgrade head
	@echo "✅ 环境就绪。请编辑 .env 填入 API Key。"

dev: serve ## 本地开发（热重载）

serve: ## 启动服务（端口默认 8000）
	$(PY) uvicorn psych_support_bot.app:app --host 0.0.0.0 --port $(PORT) --reload

test: ## 运行全量测试
	$(PY) pytest

test-unit: ## 仅运行单元测试
	$(PY) pytest tests/unit/ -v

test-integration: ## 仅运行集成测试
	$(PY) pytest tests/integration/ -v

test-eval: ## 仅运行安全评估测试
	$(PY) pytest tests/evals/ -v

lint: ## 代码风格检查（ruff）
	$(PY) ruff check src/ tests/
	$(PY) ruff format --check src/ tests/

format: ## 自动修复代码风格
	$(PY) ruff check --fix src/ tests/
	$(PY) ruff format src/ tests/

migrate: ## 执行数据库迁移
	$(PY) alembic upgrade head

migrate-new: ## 生成新迁移（需先修改 ORM model）用法: make migrate-new m="描述"
	@test -n "$(m)" || (echo "用法: make migrate-new m='描述'" && exit 1)
	$(PY) alembic revision --autogenerate -m "$(m)"

db-reset: ## 重置数据库（开发环境用，会清空数据）
	rm -f data/psych_support_bot.db
	$(PY) alembic upgrade head

deploy: ## Docker 部署（服务器）
	bash deploy.sh

clean: ## 清理构建产物和缓存
	rm -rf .pytest_cache .mypy_cache .ruff_cache __pycache__ *.egg-info dist build
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
