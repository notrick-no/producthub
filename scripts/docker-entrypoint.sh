#!/bin/sh
# 生产容器入口(见 doc/部署.md):
#   1. 启动前先把数据库 schema 升到最新——迁移是追加式,只加不改,不碰线上数据
#   2. 再起 uvicorn(Railway 通过 PORT 环境变量注入监听端口)
set -e

echo "[producthub] running database migrations (alembic upgrade head)..."
alembic upgrade head

echo "[producthub] starting uvicorn on 0.0.0.0:${PORT:-8000}..."
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}"
