"""前端构建产物的静态托管(仅生产使用)。

开发期前端走 Vite 5173(自己代理 /api、/uploads 到后端),不需要这里;
生产只跑一个 FastAPI 进程时,由它兜底托管 `frontend/dist`:

  - 命中真实文件(如 /assets/*.js、/favicon.svg)原样返回;
  - 未命中的路径若以 /api、/uploads 开头,仍抛 404(保持后端报错语义,
    不让前端页面把 API 的 404 吞成 index.html);
  - 其余未命中路径(BrowserRouter 深链接,如 /products/5 刷新)回退 index.html。
"""
from pathlib import Path

from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException

# 项目根/frontend/dist
FRONTEND_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"


class SpaStaticFiles(StaticFiles):
    """SPA 静态托管:深链接回退 index.html,/api 与 /uploads 未命中保持 404。"""

    async def get_response(self, path: str, scope):
        try:
            return await super().get_response(path, scope)
        except HTTPException as exc:
            if exc.status_code != 404:
                raise
            full = scope.get("path", "")
            if full.startswith("/api") or full.startswith("/uploads"):
                raise  # 后端管辖的路径:404 原样返回
            # 前端路由的深链接:回退到 index.html
            return await super().get_response("index.html", scope)
