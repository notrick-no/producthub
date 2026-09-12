"""登录 / 登出 / 当前用户 / 修改密码 / 邀请设密。

前缀 /api/auth。登录成功下发 HttpOnly 会话 cookie(producthub_session),
前端同源请求自动携带,无需手动挂 token。

登录态分级:
  /me、/password、set-password —— 宽松(read_session_user):must_change 期间也要能到;
  业务数据(products/categories/...)走 get_current_user,改密前被 403 挡住。
"""
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import (
    SESSION_COOKIE,
    add_session,
    clear_session_cookie,
    delete_user_sessions,
    now_utc,
    purge_expired_sessions,
    read_session_user,
    set_session_cookie,
)
from ..login_audit import prune_old, record_login
from ..models import AuthSession
from ..models import User
from ..schemas import (
    ChangePasswordRequest,
    LoginRequest,
    SetPasswordRequest,
    UserRead,
    user_read_from_model,
)
from ..security import hash_password, hash_token, verify_password

router = APIRouter(prefix="/api", tags=["auth"])


@router.post("/auth/login", response_model=UserRead)
def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
):
    """登录。三条路径**都**写审计,成功与失败都记。

    ⚠️ 失败路径上的 `db.commit()` 不是多余的:get_db(app/db.py)只负责 close(),
    **既不 commit 也不 rollback**。所以「add 一条失败记录然后 raise 401」的结果是
    这条记录被静默丢弃 —— 表里只剩成功记录,而且没有任何地方会报错。
    审计要回答的往往正是「谁在试」,这部分丢了就等于没做审计。
    """
    email = body.email.strip().lower()
    user = db.scalars(select(User).where(User.email == email)).first()
    # 未设过密码(邀请待完成)的账号也走统一报错,不暴露其状态
    if user is None or not verify_password(body.password, user.password_hash):
        # user_id 能填就填:对外的报错是统一的(不暴露账号是否存在),但审计记录是
        # 管理员才看得到的数据 —— 「有人在拿这个邮箱试密码」正是要看出的事情。
        record_login(
            db, request, email=email, user_id=user.id if user else None, succeeded=False
        )
        db.commit()
        raise HTTPException(status_code=401, detail="邮箱或密码不正确")
    if not user.is_active:
        record_login(db, request, email=email, user_id=user.id, succeeded=False)
        db.commit()
        raise HTTPException(status_code=401, detail="账号已被禁用,请联系管理员")
    record_login(db, request, email=email, user_id=user.id, succeeded=True)
    # 顺手清一次过期记录:这张表只追加,而写入点是公开且无限流的端点
    prune_old(db)
    purge_expired_sessions(db, user.id)
    raw = add_session(db, user)
    db.commit()
    set_session_cookie(response, raw)
    return user_read_from_model(user)


@router.post("/auth/logout", status_code=204)
def logout(request: Request, response: Response, db: Session = Depends(get_db)):
    """删除当前会话并清 cookie(不要求已登录:没登录也返回 204)。"""
    raw = request.cookies.get(SESSION_COOKIE)
    if raw:
        db.execute(
            delete(AuthSession).where(AuthSession.token_hash == hash_token(raw))
        )
        db.commit()
    clear_session_cookie(response)


@router.get("/auth/me", response_model=UserRead)
def me(user: User = Depends(read_session_user)):
    """当前登录用户(宽松:改密前也能拿,前端据此强制跳改密页)。"""
    return user_read_from_model(user)


@router.post("/auth/password", response_model=UserRead)
def change_password(
    body: ChangePasswordRequest,
    request: Request,
    user: User = Depends(read_session_user),
    db: Session = Depends(get_db),
):
    """改自己密码(临时密码登录后也必须走这)。保留当前会话,登出其它端。"""
    if not verify_password(body.old_password, user.password_hash):
        raise HTTPException(status_code=400, detail="原密码不正确")
    user.password_hash = hash_password(body.new_password)
    user.must_change_password = False
    current_raw = request.cookies.get(SESSION_COOKIE) or ""
    current_hash = hash_token(current_raw)
    db.execute(
        delete(AuthSession).where(
            AuthSession.user_id == user.id,
            AuthSession.token_hash != current_hash,
        )
    )
    db.commit()
    return user_read_from_model(user)


@router.post("/auth/set-password", response_model=UserRead)
def set_password(
    body: SetPasswordRequest, response: Response, db: Session = Depends(get_db)
):
    """邀请链接设初始密码(公开,一次性)。设完直接建会话登录,免二次登录。"""
    user = db.scalars(
        select(User).where(User.invite_token_hash == hash_token(body.token))
    ).first()
    if (
        user is None
        or user.invite_token_expires_at is None
        or user.invite_token_expires_at < now_utc()
    ):
        raise HTTPException(status_code=400, detail="邀请链接无效或已过期,请联系管理员重置")
    user.password_hash = hash_password(body.new_password)
    user.must_change_password = False
    user.invite_token_hash = None
    user.invite_token_expires_at = None
    # 清掉该用户历史会话(理论上邀请期还没登录过),统一走新会话
    delete_user_sessions(db, user.id)
    raw = add_session(db, user)
    db.commit()
    set_session_cookie(response, raw)
    return user_read_from_model(user)
