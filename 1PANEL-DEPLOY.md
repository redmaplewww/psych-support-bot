# 1Panel 部署指南（psych-support-bot）

## 包内容

```
psychobot-1panel-<版本>.tar.gz
├── src/                      # 应用源码（含 LLM 语义风险分类层、危机资源库）
├── data/knowledge/           # 知识库语料（心理教育内容）
├── migrations/               # Alembic 数据库迁移
├── Dockerfile.server         # 生产镜像构建文件（国内镜像源加速）
├── docker-compose.server.yml # 1Panel 编排入口
├── .env.example              # 环境变量模板（占位符，需填真实密钥）
├── deploy.sh / start.sh      # 可选：命令行部署脚本（不走 1Panel 时用）
└── 1PANEL-DEPLOY.md          # 本文件
```

## 前置条件

- 1Panel 已安装且 Docker 正常运行
- 服务器防火墙 / 安全组放行 **9958** 端口（1Panel → 主机 → 防火墙 → 放行 9958/tcp）
- 一枚可用的 LLM API Key（OpenAI 兼容接口）

## 部署步骤

### 1. 上传并解压

将 tar.gz 上传到服务器（例如 `/opt/psych-bot/`）并解压：

```bash
mkdir -p /opt/psych-bot && tar -xzf psychobot-1panel-*.tar.gz -C /opt/psych-bot
```

### 2. 配置环境变量

```bash
cd /opt/psych-bot
cp .env.example .env
vi .env   # 填入真实密钥
```

必填项：

| 变量 | 说明 |
|---|---|
| `OPENAI_API_KEY` | LLM API 密钥（OpenAI 兼容接口） |
| `OPENAI_BASE_URL` | LLM 端点，如 `https://api.example.com/v1`（SDK 会自动追加 `/chat/completions`） |
| `OPENAI_MODEL` | 模型名 |
| `LANGFUSE_*` | 可观测性（可留占位，不影响运行，仅无追踪数据） |

### 3. 在 1Panel 创建编排

1Panel → **容器 → 编排 → 创建编排**：
- 来源选择"路径"，路径填 `/opt/psych-bot/docker-compose.server.yml`
- 1Panel 会自动读取同目录的 `.env` 作为变量来源
- 确认后 1Panel 自动 build + 启动（首次构建需拉取 python:3.12-slim 并安装依赖，国内走阿里云镜像源，约 3-5 分钟）

### 4. 验证

```bash
curl http://127.0.0.1:9958/health          # {"status":"ok"} 即健康
curl http://127.0.0.1:9958/system/info     # 返回应用信息即正常
```

浏览器访问 `http://<服务器IP>:9958` 即可进入对话界面。

M1-M3 记录与洞察接口（同进程一并生效）：

```bash
curl "http://127.0.0.1:9958/v1/assessments?user_id=demo"        # 问卷历史
curl "http://127.0.0.1:9958/v1/checkins?user_id=demo&days=30"   # 打卡历史
curl "http://127.0.0.1:9958/v1/exercises/records?user_id=demo"  # 练习历史
```

## 常量与数据

| 项 | 值 |
|---|---|
| 容器名 | `psych-bot` |
| 宿主端口 | `9958` → 容器 `8000` |
| 数据库 | SQLite（`bot-data` 卷，持久化于 `/app/data`） |
| 日志 | `bot-logs` 卷（`/app/logs`） |
| 重启策略 | `unless-stopped` |

升级版本：替换源码目录内容 → 1Panel 编排页面点"重新构建 / 重新部署"。SQLite 数据在 `bot-data` 卷中，重建容器不丢失。

## 安全提示

- 当前版本**未含鉴权**（demo 阶段决策）：请勿将 9958 暴露给不可信网络；建议仅内网/VPN 访问，或前置 Nginx 加 Basic Auth
- `.env` 含密钥，注意权限（`chmod 600 .env`），不要提交到 git
- `ALLOWED_ORIGINS` 生产环境建议从 `*` 收紧为实际前端地址
