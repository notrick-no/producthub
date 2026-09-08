"""数据库引擎与会话。

连接串从项目根目录的 .env 读取(DATABASE_URL)。
"""
import os
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

# 读取项目根目录的 .env
BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+psycopg://postgres:postgres@localhost:5432/producthub",
)

engine = create_engine(DATABASE_URL, pool_pre_ping=True)

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Session:
    """FastAPI 依赖:每个请求一个会话,用完自动关闭。"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
