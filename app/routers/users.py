"""管理员账号管理(整个 router 都要求 admin 角色)。

员工账号流程:
  创建(POST)  → 生成一次性邀请 token(72h)并发邀请邮件,员工点链接自设密码;
  重置密码    → 生成临时密码发邮件 + must_change_password=True(强制下次登录改)+ 踢掉全部会话;
  启停(PATCH) → is_active=False 即离职冻结,立即踢下线。

护栏:最后一个「启用中」的管理员不允许被禁用或重置密码,避免管理员全部锁死。
本期不做 employee↔admin 角色升降级(邮箱也不可变,它是唯一登录 ID)。
"""
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import mailer
from ..db import get_db
from ..deps import delete_user_sessions, now_utc, require_admin
from ..models import User
from ..schemas import UserCreate, UserRead, UserUpdate, user_read_from_model
from ..security import hash_password, make_temp_password, new_invite_token

INVITE_TTL = timedelta(hours=72)  # 邀请链接有效期

router = APIRouter(
    prefix="/api", tags=["users"], dependencies=[Depends(require_admin)]
)


def _count_active_admins(db: Session) -> int:
    return (
        db.scalar(
            select(func.count())
            .select_from(User)
            .where(User.role == "admin", User.is_active.is_(True))
        )
        or 0
    )


def _is_last_active_admin(db: Session, user: User) -> bool:
    """目标是「最后一个启用中的管理员」→ 禁用/重置会把它挡在门外,不允许。"""
    return user.role == "admin" and user.is_active and _count_active_admins(db) == 1


@router.get("/users", response_model=list[UserRead])
def list_users(db: Session = Depends(get_db)):
    """所有账号(含禁用;是否已设密在 UserRead.password_set)。"""
    users = db.scalars(select(User).order_by(User.id)).all()
    return [user_read_from_model(u) for u in users]


@router.post("/users", response_model=UserRead, status_code=201)
def create_user(body: UserCreate, db: Session = Depends(get_db)):
    """创建员工账号并发送邀请邮件(邮件没发成功则整体回滚,不留半截账号)。"""
    if not mailer.is_mail_configured():
        raise HTTPException(
            status_code=503,
            detail="SMTP 未配置,无法发送邀请邮件,请在环境变量配置 SMTP_HOST 与 SMTP_FROM",
        )

    user = User(
        email=body.email,
        name=body.name,
        department=body.department,
        role="employee",
        is_active=True,
    )
    raw, token_hash = new_invite_token()
    user.invite_token_hash = token_hash
    user.invite_token_expires_at = now_utc() + INVITE_TTL
    db.add(user)
    try:
        db.flush()  # 提前触发 email 唯一约束
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="该邮箱已注册")

    try:
        mailer.send_user_invite(user.email, user.name, raw)
    except (mailer.MailNotConfigured, mailer.MailSendError) as exc:
        db.rollback()
        raise HTTPException(status_code=503, detail=str(exc))

    db.commit()
    db.refresh(user)
    return user_read_from_model(user)


@router.patch("/users/{user_id}", response_model=UserRead)
def update_user(user_id: int, body: UserUpdate, db: Session = Depends(get_db)):
    """部分更新:本期支持 name / department / is_active。缺席的键不改。"""
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="员工不存在")

    data = body.model_dump(exclude_unset=True)

    if "name" in data and data["name"]:
        user.name = data["name"]
    if "department" in data:  # null = 清空
        user.department = data["department"]

    if "is_active" in data:
        active = bool(data["is_active"])
        if not active and _is_last_active_admin(db, user):
            raise HTTPException(
                status_code=400, detail="不能禁用最后一个启用的管理员"
            )
        if user.is_active and not active:
            delete_user_sessions(db, user.id)  # 离职冻结:立即踢下线
        user.is_active = active

    db.commit()
    db.refresh(user)
    return user_read_from_model(user)


@router.post("/users/{user_id}/reset-password")
def reset_password(user_id: int, db: Session = Depends(get_db)):
    """重置为随机临时密码 → 发邮件 + 强制下次登录修改 + 踢掉全部会话。"""
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="员工不存在")
    if _is_last_active_admin(db, user):
        raise HTTPException(
            status_code=400, detail="不能重置最后一个启用管理员的密码"
        )
    if not mailer.is_mail_configured():
        raise HTTPException(
            status_code=503,
            detail="SMTP 未配置,无法发送邮件,请在环境变量配置 SMTP_HOST 与 SMTP_FROM",
        )

    temp = make_temp_password()
    user.password_hash = hash_password(temp)
    user.must_change_password = True
    user.invite_token_hash = None
    user.invite_token_expires_at = None
    delete_user_sessions(db, user.id)  # 重置 = 强制重登
    try:
        db.flush()
        mailer.send_reset_password(user.email, user.name, temp)
    except (mailer.MailNotConfigured, mailer.MailSendError) as exc:
        db.rollback()
        raise HTTPException(status_code=503, detail=str(exc))

    db.commit()
    return {"ok": True, "email": user.email}
