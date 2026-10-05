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

语音功能（可选，不配则前端自动降级为纯文字/浏览器朗读）：

| 变量 | 说明 |
|---|---|
| `VOICE_STT_PROVIDER` | `minimax`（推荐，语音转文字）/ `dots` / `openai` |
| `VOICE_STT_API_KEY` | STT 密钥（minimax 与 TTS 可同一把；base_url/model 有缺省） |
| `VOICE_TTS_PROVIDER` | `minimax`（双向流式合成）/ `openai` |
| `VOICE_TTS_API_KEY` | TTS 密钥 |
| `VOICE_TTS_VOICE` | 音色，如 `Chinese (Mandarin)_Warm_Bestie` |

> 完整语音变量见 `.env.example` 的「语音 I/O」段（STT/TTS 与主 LLM 分离配置，便于换供应商）。

访问控制（生产建议开启）：

| 变量 | 说明 |
|---|---|
| `AUTH_ENABLED` | `true` 启用 JWT 鉴权（游客进入前端仍自动静默注册，无需表单） |
| `JWT_SECRET_KEY` | 开启鉴权时必填；留空则每次启动随机生成（重启使旧 token 失效） |

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

「我」页数据管理接口（20260906 起）：

```bash
curl "http://127.0.0.1:9958/v1/me/summary?user_id=demo"   # 账号事实 + 近30天计数
curl -D - -o me.json "http://127.0.0.1:9958/v1/me/export?user_id=demo"  # 全量导出
# 清空记录 / 注销账号：POST /v1/me/confirm-intent 换取确认令牌后
# DELETE /v1/me/records 或 /v1/me/account（两步式确认，详见前端「我」页交互）
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

### 升级生效自检（重要，避免"部署了但没变化"）

1Panel 的「重新部署/重新启动」只重建容器（复用旧镜像），**不会重新构建**——源码更新后必须走「重新构建」。命令行等价操作：

```bash
cd /opt/psych-bot
# 1. 确认源码确实是新版（对照本地版本指纹，见下）
grep -c "guestPassword" src/psych_support_bot/static/index.html   # ≥1 即新版
# 2. 强制重建镜像（--no-cache 排除一切构建缓存）
docker compose -f docker-compose.server.yml build --no-cache app
docker compose -f docker-compose.server.yml up -d
# 3. 验证线上页面已是新版
curl -s http://127.0.0.1:9958/ | grep -c "guestPassword"          # ≥1 即生效
```

> 常见坑：只点「重新部署」而不重新构建、或上传了新 tar.gz 但忘记解压覆盖——
> 两者都会导致"部署成功但功能没变化"。用第 3 步的指纹命令一验便知。

## 安全提示

- 访问控制：设 `AUTH_ENABLED=true` + `JWT_SECRET_KEY=<随机长串>` 启用 JWT 鉴权（前端游客静默注册，无需表单；后端接口无 token 一律 401）。demo 阶段默认关闭，公网部署务必开启
- 语音接口 `/v1/voice/*` 同样受鉴权守卫保护（音频即转即弃，不落库）
- `.env` 含密钥，注意权限（`chmod 600 .env`），不要提交到 git
- `ALLOWED_ORIGINS` 生产环境建议从 `*` 收紧为实际前端地址
- compose 用 `env_file: .env` 整体透传，新增环境变量只改 `.env` 即可，无需改 compose
