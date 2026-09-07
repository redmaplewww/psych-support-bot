#!/usr/bin/env bash
set -euo pipefail

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

info()  { echo -e "${GREEN}[INFO]${NC} $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $*"; }
err()   { echo -e "${RED}[ERROR]${NC} $*"; }

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo "======================================"
echo "  心理学Bot 服务器部署"
echo "======================================"
echo ""

check_docker() {
    if ! command -v docker &>/dev/null; then
        err "未检测到 Docker，正在尝试安装..."
        curl -fsSL https://get.docker.com | sh
        systemctl start docker
        systemctl enable docker
        info "Docker 安装完成"
    fi

    if ! docker compose version &>/dev/null; then
        err "未检测到 docker compose 插件"
        exit 1
    fi
    info "Docker 环境检查通过"
}

check_env() {
    if [ ! -f .env ]; then
        if [ -f .env.example ]; then
            cp .env.example .env
            warn "已从 .env.example 创建 .env，请检查 API Key 配置"
        else
            err "缺少 .env 配置文件"
            exit 1
        fi
    fi
    info "配置文件检查通过"
}

build_and_start() {
    info "正在构建镜像（首次可能需要几分钟）..."
    docker compose -f docker-compose.server.yml build

    info "正在启动服务..."
    docker compose -f docker-compose.server.yml up -d

    info "等待服务就绪..."
    local retries=0
    local max_retries=30
    while [ $retries -lt $max_retries ]; do
        if curl -sf http://127.0.0.1:9958/health >/dev/null 2>&1; then
            echo ""
            info "服务已成功启动！"
            echo ""
            echo "  访问地址: http://<你的服务器IP>:9958"
            echo ""
            echo "  查看日志: docker compose -f docker-compose.server.yml logs -f"
            echo "  停止服务: docker compose -f docker-compose.server.yml down"
            echo ""
            return 0
        fi
        retries=$((retries + 1))
        sleep 2
    done

    err "服务启动超时，请检查日志："
    echo "  docker compose -f docker-compose.server.yml logs"
    return 1
}

open_firewall() {
    if command -v ufw &>/dev/null; then
        sudo ufw allow 9958/tcp 2>/dev/null && info "已开放防火墙端口 9958 (ufw)" || true
    elif command -v firewall-cmd &>/dev/null; then
        sudo firewall-cmd --permanent --add-port=9958/tcp 2>/dev/null && sudo firewall-cmd --reload && info "已开放防火墙端口 9958 (firewalld)" || true
    elif command -v iptables &>/dev/null; then
        sudo iptables -I INPUT -p tcp --dport 9958 -j ACCEPT 2>/dev/null && info "已开放防火墙端口 9958 (iptables)" || true
    fi
}

check_docker
check_env
open_firewall
build_and_start
