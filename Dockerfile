# producthub 生产镜像(Railway 等平台用根目录 Dockerfile 云端构建)
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
# ⚠️ **基础镜像必须是 glibc ≥ 2.28 的**(debian 系)。AI 内核 dsh 的可执行体是按
# `manylinux_2_28` 分发的,alpine(musl)装上轮子也跑不起来 —— 而那个错误不会在
# 构建时出现,要等到第一次提问才炸。下面那条 dsh_runtime_check 就是为了把它提前。
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
COPY scripts/dsh_runtime_check.py ./scripts/dsh_runtime_check.py
RUN chmod +x scripts/docker-entrypoint.sh

# dsh 的工作目录:会话 JSONL、profile、按提问者生成的 patch。
#
# ⚠️ **必须可写**,而且**不要挂卷**:里面存的是被我们刻意放弃的历史
# (上下文由 Postgres 重放,见 ai_harness 模块头),Railway 的磁盘本来就是易失的。
# 写在默认位置(/app/.dsh),`AI_DSH_HOME` 显式写出来只是为了让它在
# `docker inspect` / Railway 面板里看得见。仓库里那份 `.dsh/` 在 .gitignore 里,
# 所以镜像里的这个是**全新的空目录** —— 干净目录能不能自举出 profile 这件事
# 由下面的检查当场证明,不靠「我本地跑过」。
ENV AI_DSH_HOME=/app/.dsh
RUN mkdir -p /app/.dsh

# 把 dsh 的运行时**在构建时**验一遍:找不到可执行体、架构不对、glibc 太旧、
# 干净目录自举不出 profile —— 这四种都会在这里变成构建失败,而不是线上第一次
# 提问时的一句「生成回答时出错」。它不需要 API key(initialize 不打上游)。
RUN python scripts/dsh_runtime_check.py

# 前端构建产物(与本地 layout 一致:frontend/dist)
COPY --from=frontend-build /app/frontend/dist ./frontend/dist

EXPOSE 8000

CMD ["/app/scripts/docker-entrypoint.sh"]
