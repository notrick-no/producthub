"""producthub API 入口。

开发(项目根目录):
    uvicorn app.main:app --reload        # 只起后端,前端另跑 Vite dev(代理到 8000)

生产(同进程托管前端):
    uvicorn app.main:app --host 0.0.0.0  # 若 frontend/dist 存在,SPA 由本服务兜底托管

路由顺序:
    /api/* 路由 → /uploads 图片静态 → (仅当 dist 存在) "/" SPA 兜底

认证(第三版):业务 /api(products/categories/requirements/users,以及第四版的
activity/summary)都要登录,登录态走 HttpOnly 会话
cookie;公开的只有 /api/auth/*、/api/health。启动 lifespan 在「库空 + env 配了
ADMIN_EMAIL/ADMIN_PASSWORD」时自动建首个管理员。
"""
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from contextlib import asynccontextmanager

from app.bootstrap import ensure_bootstrap_admin
from app.ai_agent import sweep_stale_runs
from app import ai_harness
from app.db import SessionLocal
from app.routers import (
    ai,
    auth,
    blog,
    categories,
    comments,
    home,
    products,
    requirements,
    users,
)
from app.static_assets import FRONTEND_DIST, SpaStaticFiles
from app.storage import UPLOAD_DIR


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # 空库且 env 配了 ADMIN_EMAIL/ADMIN_PASSWORD → 建首个管理员(幂等,见 app/bootstrap.py)
    with SessionLocal() as db:
        ensure_bootstrap_admin(db)
    # 上一次进程被杀时留在 running 的 AI 回答,在这里收尾(见 app/ai_agent.py)
    sweep_stale_runs()
    yield
    # 关掉池子里所有 dsh 实例(每个 = 1 个 node 子进程 + 1 个 MCP 子进程)。
    # ⚠️ **不关的后果不是"优雅不优雅"**:那些子进程的父进程没了之后会各自被
    # 重新挂到 init 上继续活着 —— 反复重启/发版之后容器里会攒下一堆孤儿进程,
    # 每个都还揣着一份 API key 和数据库连接。Railway 每次发版都重启,所以这条
    # 路径是**常规路径**,不是异常路径。
    # 放在 yield 之后 = 正常关闭与收到信号时都会走到;它自己吞掉所有异常
    # (见 ai_harness.shutdown_all),关不掉也不该拦住进程退出。
    ai_harness.shutdown_all()


app = FastAPI(title="producthub API", version="0.1.0", lifespan=lifespan)

app.include_router(auth.router)
app.include_router(categories.router)
app.include_router(products.router)
app.include_router(requirements.router)
app.include_router(users.router)
app.include_router(comments.router)  # 评论 / 点赞(第五版)
app.include_router(blog.router)  # 博客:帖子 / 标签 / 点赞(第五版)
app.include_router(home.router)  # 首页动态 / 汇总(第四版)
app.include_router(ai.router)  # AI 问答(第六版)


@app.get("/api/health")
def health():
    return {"status": "ok"}


# DB 里图片 path 存的就是 /uploads/<随机名>.<扩展名>,这里把目录挂成静态可访问
app.mount("/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")


# 生产兜底托管前端构建产物:构建产物存在才挂,开发时前端走 Vite 不受影响
if (FRONTEND_DIST / "index.html").exists():
    app.mount("/", SpaStaticFiles(directory=FRONTEND_DIST, html=True), name="spa")
