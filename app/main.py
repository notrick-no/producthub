"""producthub API 入口。

开发(项目根目录):
    uvicorn app.main:app --reload        # 只起后端,前端另跑 Vite dev(代理到 8000)

生产(同进程托管前端):
    uvicorn app.main:app --host 0.0.0.0  # 若 frontend/dist 存在,SPA 由本服务兜底托管

路由顺序:
    /api/* 路由 → /uploads 图片静态 → (仅当 dist 存在) "/" SPA 兜底

认证(第三版):业务 /api(products/categories/users)都要登录,登录态走 HttpOnly 会话
cookie;公开的只有 /api/auth/*、/api/health。启动 lifespan 在「库空 + env 配了
ADMIN_EMAIL/ADMIN_PASSWORD」时自动建首个管理员。
"""
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from contextlib import asynccontextmanager

from app.bootstrap import ensure_bootstrap_admin
from app.db import SessionLocal
from app.routers import auth, categories, products, users
from app.static_assets import FRONTEND_DIST, SpaStaticFiles
from app.storage import UPLOAD_DIR


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # 空库且 env 配了 ADMIN_EMAIL/ADMIN_PASSWORD → 建首个管理员(幂等,见 app/bootstrap.py)
    with SessionLocal() as db:
        ensure_bootstrap_admin(db)
    yield


app = FastAPI(title="producthub API", version="0.1.0", lifespan=lifespan)

app.include_router(auth.router)
app.include_router(categories.router)
app.include_router(products.router)
app.include_router(users.router)


@app.get("/api/health")
def health():
    return {"status": "ok"}


# DB 里图片 path 存的就是 /uploads/<随机名>.<扩展名>,这里把目录挂成静态可访问
app.mount("/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")


# 生产兜底托管前端构建产物:构建产物存在才挂,开发时前端走 Vite 不受影响
if (FRONTEND_DIST / "index.html").exists():
    app.mount("/", SpaStaticFiles(directory=FRONTEND_DIST, html=True), name="spa")
