"""Alembic 迁移环境。

连接串与元数据都来自应用代码(app.db / app.models),避免 .ini 里双份维护:
  - 连接串取 app.db.DATABASE_URL(它已归一驱动后缀,且读环境变量/根目录 .env)
  - 迁移目标是 app.models.Base.metadata 注册的当前全部表

运行方式(项目根目录):
  alembic upgrade head      # 空库建全表 / 旧库增量升级
  alembic stamp head        # 已有建好表的库,只打版本标记不重跑
  alembic revision --autogenerate -m "描述"   # 模型改了之后生成增量迁移
"""
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from app import models  # noqa: F401  导入即注册全部表到 metadata
from app.db import DATABASE_URL
from app.models import Base

config = context.config

# 把应用里的连接串塞给 alembic(优先于 alembic.ini 的占位值)
config.set_main_option("sqlalchemy.url", DATABASE_URL)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """离线模式:不连数据库,只生成 SQL。"""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """在线模式:连库执行迁移。"""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
