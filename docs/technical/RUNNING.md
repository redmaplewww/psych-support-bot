# Running The Backend

## Local

```bash
uv sync
uv run alembic upgrade head
uv run uvicorn psych_support_bot.app:app --reload
```

## Tests

```bash
uv run pytest
uv run psych-support-bot-evals
```

## Docker（服务器部署）

```bash
bash deploy.sh          # 构建镜像并启动（Dockerfile.server + docker-compose.server.yml）
./stop.sh && ./start.sh # 启停控制
```

完整流程见根目录《服务器部署说明.md》。
