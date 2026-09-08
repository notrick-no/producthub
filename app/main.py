"""Producthut API 入口。

启动(项目根目录):
    uvicorn app.main:app --reload

图片文件以 /uploads 静态托管(实际目录见 app/storage.py 的 uploads/)。
"""
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.routers import categories, products
from app.storage import UPLOAD_DIR

app = FastAPI(title="Producthut API", version="0.1.0")

app.include_router(categories.router)
app.include_router(products.router)


@app.get("/api/health")
def health():
    return {"status": "ok"}


# DB 里图片 path 存的就是 /uploads/<随机名>.<扩展名>,这里把目录挂成静态可访问
app.mount("/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")
