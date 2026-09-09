"""首个管理员引导(启动时调用)。

没有公开注册入口,库是空的、且环境变量配了 ADMIN_EMAIL / ADMIN_PASSWORD 时,
在启动(lifespan)里自动创建第一个管理员;之后管理员可在界面里建员工账号。

幂等:已有任何账号就不再创建;两个进程并发抢建由 email 唯一约束兜底。
若表还没建(没跑迁移 / 直接起 uvicorn)静默跳过,不让启动崩。
"""
import logging
import os

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from .models import User
from .security import hash_password

log = logging.getLogger("producthub")


def ensure_bootstrap_admin(db) -> None:
    email = os.getenv("ADMIN_EMAIL")
    password = os.getenv("ADMIN_PASSWORD")
    if not email or not password:
        return  # 没配就跳过(本地开发一般用别的方式造管理员)

    try:
        existing = db.scalar(select(func.count()).select_from(User))
    except Exception as exc:  # noqa: BLE001 — 表未建/迁移未跑/连不上都别让启动崩
        log.warning("[producthub] 管理员引导跳过(表可能尚未创建):%s", exc)
        return

    if existing:
        return  # 库里已有账号,不重复建

    db.add(
        User(
            email=email.strip().lower(),
            name=os.getenv("ADMIN_NAME", "管理员"),
            role="admin",
            department=None,
            is_active=True,
            must_change_password=False,
            password_hash=hash_password(password),
        )
    )
    try:
        db.commit()
        log.info("[producthub] 已创建首个管理员账号:%s", email)
    except IntegrityError:
        db.rollback()  # 并发下别人已建,忽略
