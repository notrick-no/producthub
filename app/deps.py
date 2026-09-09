"""鉴权依赖与会话管理(服务端 sessions 表 + HttpOnly cookie)。

依赖分层(同一请求内 FastAPI 会缓存子依赖,会话只查一次):
  read_session_user —— 宽松:仅校验「有有效登录」(登录态已过期的、被禁用的都 401);
  get_current_user  —— 在宽松基础上,若 must_change_password 则 403「请先修改密码」
                       (重置密码后的硬门禁:改密前访问不到业务数据);
  require_admin     —— get_current_user + role == admin,否则 403。

cookie:HttpOnly + SameSite=Lax;仅当 APP_BASE_URL 为 https 才带 Secure(本地 http 可登)。
无 JWT、无 SECRET_KEY、无 CSRF token——SameSite=Lax 对同源内部工具足够。
"""
from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException, Request, Response
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .db import get_db
from .models import Session as AuthSession
from .models import User
from .security import app_base_url, hash_token, new_session_token

SESSION_COOKIE = "producthub_session"
SESSION_TTL = timedelta(days=30)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


# ---------- 会话读写 ----------

def _cookie_secure() -> bool:
    """仅 https 站点给 cookie 打 Secure;本地 http://localhost 才能带 cookie 登录。"""
    return app_base_url().startswith("https://")


def add_session(db: Session, user: User) -> str:
    """为该用户建一条会话(未提交),返回明文 token;由调用方决定何时 commit。"""
    raw, token_hash = new_session_token()
    db.add(
        AuthSession(
            user_id=user.id,
            token_hash=token_hash,
            expires_at=now_utc() + SESSION_TTL,
        )
    )
    return raw


def set_session_cookie(response: Response, raw_token: str) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        raw_token,
        httponly=True,
        samesite="lax",
        secure=_cookie_secure(),
        max_age=int(SESSION_TTL.total_seconds()),
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE, path="/")


def delete_user_sessions(db: Session, user_id: int) -> None:
    """踢掉该用户全部会话(禁用员工 / 重置密码时用)。"""
    db.execute(delete(AuthSession).where(AuthSession.user_id == user_id))


def purge_expired_sessions(db: Session, user_id: int) -> None:
    db.execute(
        delete(AuthSession).where(
            AuthSession.user_id == user_id, AuthSession.expires_at < now_utc()
        )
    )


# ---------- 依赖 ----------

def read_session_user(request: Request, db: Session = Depends(get_db)) -> User:
    """宽松登录态:cookie → sessions 表 → user;无效/过期/禁用一律 401。"""
    raw = request.cookies.get(SESSION_COOKIE)
    if not raw:
        raise HTTPException(status_code=401, detail="未登录")
    auth_session = db.execute(
        select(AuthSession).where(AuthSession.token_hash == hash_token(raw))
    ).scalar_one_or_none()
    if auth_session is None:
        raise HTTPException(status_code=401, detail="登录已失效,请重新登录")
    if auth_session.expires_at < now_utc():
        db.delete(auth_session)
        db.commit()
        raise HTTPException(status_code=401, detail="登录已过期,请重新登录")
    user = auth_session.user
    if user is None or not user.is_active:
        raise HTTPException(status_code=401, detail="账号不可用,请联系管理员")
    return user


def get_current_user(user: User = Depends(read_session_user)) -> User:
    """业务数据用登录态:改密前(must_change_password)一律 403 挡住。"""
    if user.must_change_password:
        raise HTTPException(status_code=403, detail="请先修改密码后再继续")
    return user


def require_admin(user: User = Depends(get_current_user)) -> User:
    """管理员专属路由依赖。"""
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="需要管理员权限")
    return user
