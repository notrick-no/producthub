"""登录审计:写 login_events(第五版)。

只做两件事 —— 记一行、清老行。**都不 commit**,由调用方(login 端点)决定事务边界;
理由见下面 `record_login` 的注释。

保留期清理为什么不挂调度器:这是一张只追加的表,唯一的写入点是公开的登录端点,
在成功登录时顺手删一次就足以把规模封顶。为一个「删旧行」引一套 cron/APScheduler,
是拿一个新组件换一次 `DELETE`。
"""
from datetime import timedelta

from sqlalchemy import delete
from sqlalchemy.orm import Session

from .deps import now_utc
from .models import LoginEvent
from .request_ip import client_ip

# 保留期。审计的价值随时间衰减,而这张表随时可以被匿名请求灌行 ——
# 半年足够回答「最近谁在登、谁在试」这类问题。
RETENTION = timedelta(days=180)

UA_MAX_LEN = 255  # 与 models.LoginEvent.user_agent 的列宽一致


def record_login(
    db: Session,
    request,
    *,
    email: str,
    user_id: int | None,
    succeeded: bool,
) -> None:
    """记一条登录尝试(不提交)。

    `email` 传进来时应当已经规范化(小写、去空白),与 users.email 的存法一致;
    失败时它可能是库里压根不存在的邮箱,这正是要记下来的东西。
    """
    ua = request.headers.get("user-agent")
    db.add(
        LoginEvent(
            user_id=user_id,
            email=email,
            ip=client_ip(request),
            # 不截断的话,一个超长 UA 会让 Postgres 抛 StringDataRightTruncation ——
            # 而这是**公开**端点,等于陌生人拿一个头就能让我们 500。
            user_agent=ua[:UA_MAX_LEN] if ua else None,
            succeeded=succeeded,
        )
    )


def prune_old(db: Session) -> None:
    """删掉保留期之前的记录(不提交)。"""
    db.execute(
        delete(LoginEvent).where(LoginEvent.created_at < now_utc() - RETENTION)
    )
