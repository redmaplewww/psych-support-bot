#!/usr/bin/env bash
# 打 1Panel 部署包（包内容规格见 1PANEL-DEPLOY.md「包内容」节）。
# 产出：dist/psychobot-1panel-<日期>.tar.gz
#
# 注意：包内只含 .env.example（占位符），绝不打包真实 .env / SQLite 数据库。
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

PKG_DATE="$(date +%Y%m%d)"
PKG_NAME="psychobot-1panel-$PKG_DATE"
STAGE="$(mktemp -d /tmp/psychobot-pkg.XXXXXX)"
trap 'rm -rf "$STAGE"' EXIT

mkdir -p "$STAGE/$PKG_NAME/data" "$STAGE/$PKG_NAME/dist_placeholder" 2>/dev/null || true
rmdir "$STAGE/$PKG_NAME/dist_placeholder" 2>/dev/null || true

# 源码（排除缓存/本地数据）
cp -R src "$STAGE/$PKG_NAME/src"
find "$STAGE/$PKG_NAME/src" -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true
find "$STAGE/$PKG_NAME/src" -name "*.pyc" -delete 2>/dev/null || true
find "$STAGE/$PKG_NAME/src" \( -name ".mimosa" -o -name ".zcode" -o -name ".DS_Store" \) -exec rm -rf {} + 2>/dev/null || true

# 知识库语料 + 迁移 + 编排/构建文件 + 部署脚本与文档
cp -R data/knowledge "$STAGE/$PKG_NAME/data/knowledge"
# 音色复刻素材（20260912）：服务端切复刻音色时的内联参考样本
if [ -d data/voice ]; then
  cp -R data/voice "$STAGE/$PKG_NAME/data/voice"
fi
cp -R migrations "$STAGE/$PKG_NAME/migrations"
find "$STAGE/$PKG_NAME/migrations" -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true
find "$STAGE/$PKG_NAME/migrations" -name "*.pyc" -delete 2>/dev/null || true
cp alembic.ini uv.lock pyproject.toml Dockerfile.server docker-compose.server.yml \
   .env.example deploy.sh start.sh stop.sh 1PANEL-DEPLOY.md "$STAGE/$PKG_NAME/"
# 复刻样本再生成工具（运维工具，非运行时依赖）
if [ -f scripts/make_bestie_samples.py ]; then
  mkdir -p "$STAGE/$PKG_NAME/scripts"
  cp scripts/make_bestie_samples.py "$STAGE/$PKG_NAME/scripts/"
fi

mkdir -p dist
# macOS bsdtar 会把扩展属性打成 ._ 开头的 AppleDouble 文件，服务器解压后
# 全是垃圾；COPYFILE_DISABLE=1 关闭该行为，--exclude 双保险（顺带清走
# 工作区可能混入的 .DS_Store）
export COPYFILE_DISABLE=1
tar -czf "dist/$PKG_NAME.tar.gz" --exclude='._*' --exclude='.DS_Store' -C "$STAGE" "$PKG_NAME"

SIZE=$(du -h "dist/$PKG_NAME.tar.gz" | cut -f1 | tr -d ' ')
echo "打包完成: dist/$PKG_NAME.tar.gz ($SIZE)"
echo "校验包内容:"
tar -tzf "dist/$PKG_NAME.tar.gz" | head -12
