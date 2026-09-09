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

# Railway 等平台注入的 DATABASE_URL 形如 postgresql://user:pass@host:port/db(不带驱动后缀);
# 本地 .env 用 postgresql+psycopg://。这里把"裸 postgresql://"补成 psycopg3 驱动,两种环境都能连。
_raw_url = os.getenv(
    "DATABASE_URL",
    "postgresql+psycopg://postgres:postgres@localhost:5432/producthub",
)
if _raw_url.startswith("postgresql://"):
    _raw_url = "postgresql+psycopg://" + _raw_url[len("postgresql://"):]
DATABASE_URL = _raw_url

engine = create_engine(DATABASE_URL, pool_pre_ping=True)

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Session:
    """FastAPI 依赖:每个请求一个会话,用完自动关闭。"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
