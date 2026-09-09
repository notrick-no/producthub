# Producthut 生产镜像(Railway 等平台用根目录 Dockerfile 云端构建)
#
# 阶段 1(node):构建前端 dist
# 阶段 2(python):装依赖 + 拷贝代码/迁移 + 前端产物;入口先迁移再起 uvicorn
# 见 doc/部署.md(发布、迁移、uploads 卷)。

# ---------- 阶段 1:构建前端 ----------
FROM node:22-alpine AS frontend-build
WORKDIR /app/frontend

# 先只拷清单文件,利用层缓存装依赖(锁文件版本 9.0,本地 pnpm 11)
COPY frontend/package.json frontend/pnpm-lock.yaml ./
RUN corepack enable \
    && corepack prepare pnpm@11 --activate \
    && pnpm install --frozen-lockfile

COPY frontend/ ./
RUN pnpm build

# ---------- 阶段 2:后端运行 ----------
FROM python:3.10-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt ./
RUN pip install -r requirements.txt

COPY app/ ./app/
COPY migrations/ ./migrations/
COPY alembic.ini ./
COPY scripts/docker-entrypoint.sh ./scripts/docker-entrypoint.sh
RUN chmod +x scripts/docker-entrypoint.sh

# 前端构建产物(与本地 layout 一致:frontend/dist)
COPY --from=frontend-build /app/frontend/dist ./frontend/dist

EXPOSE 8000

CMD ["/app/scripts/docker-entrypoint.sh"]
