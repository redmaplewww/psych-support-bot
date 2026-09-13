#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
docker compose -f docker-compose.server.yml up -d
echo "服务已启动"
echo "访问地址: http://<服务器IP>:9958"
echo "查看日志: docker compose -f docker-compose.server.yml logs -f"
