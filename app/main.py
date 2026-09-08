"""Producthut API 入口。

启动(项目根目录):
    uvicorn app.main:app --reload
"""
from fastapi import FastAPI

from app.routers import categories, products

app = FastAPI(title="Producthut API", version="0.1.0")

app.include_router(categories.router)
app.include_router(products.router)


@app.get("/api/health")
def health():
    return {"status": "ok"}
