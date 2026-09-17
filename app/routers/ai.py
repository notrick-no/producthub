"""AI 问答(第六版)。

路由挂 /api 前缀:
  GET    /api/ai/status                        当前用户能不能问、还能问几问
  GET    /api/ai/conversations                 我的会话列表
  POST   /api/ai/conversations                 新建会话
  GET    /api/ai/conversations/{id}            会话详情(含全部消息)
  DELETE /api/ai/conversations/{id}            删会话
  POST   /api/ai/conversations/{id}/messages   提问(**SSE 流式**)★
  GET    /api/ai/settings                      限额设置(管理员)
  PUT    /api/ai/settings                      改设置(管理员)
  GET    /api/ai/usage                         用量报表(管理员,只有数字没有内容)

**会话只本人可见,管理员也不行**(见 permissions.can_view_ai_conversation)。
看不见时返回 **404** 而不是 403 —— 403 等于承认「有这么个会话」,同博客草稿的规矩。

本版**不新增公开面**:全部端点在登录态之后。

--------------------------------------------------------------------------
★ 那个端点为什么长成那样:数据库连接必须在调模型之前还回池子
--------------------------------------------------------------------------

`get_db`(app/db.py)只 `close()`,从不中途提交;而全仓库**没有任何地方在请求中途
手动 `db.close()`**。这个假设对现状成立,因为最慢的端点(发信)15–25 秒且不查库。

`engine` 只配了 `pool_pre_ping=True` → 默认 QueuePool = **5 + 10 = 15 条连接**。
一次 AI 问答要持有一两分钟。**第 16 个并发提问起,全站每一个查库的端点都会排队,
然后撞 `pool_timeout` 报错** —— 包括那些完全不碰 AI 的人。这是这一版最要命的一处,
所以 ask() 里那段「短事务 → commit → **close**」不是优化,是必须。

配套的两件事:
  · 模型循环期间**不持有会话**,每个工具调用各自开一个短命 `SessionLocal()`
    (见 app/ai_agent.py);
  · 提问者传给工具的是 **`ai_tools.Viewer` 快照**而不是 ORM 的 `User` ——
    close() 之后对象是 detached 的,读 `id`/`role`/`name` 恰好还能用,但任何一次
    懒加载都会在某个工具深处炸。快照让这件事在类型上就不可能发生。

`get_db` 的 `finally: db.close()` 会再关一次 —— SQLAlchemy 的 `close()` 幂等,无害。

⚠️ **端点必须是同步 `def`。** `urllib` 是阻塞的;写成 `async def` 会把阻塞调用
放到**事件循环**上,那才是真的全站挂起。同步 `def` 由 FastAPI 丢进 anyio 线程池
(默认 40),同步生成器由 Starlette 用 `iterate_in_threadpool` 迭代,安全。
并发上限因此是线程池的 40 —— **不是** DB 池的 15,因为连接已经还回去了。
"""
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import ai_agent, ai_client, permissions
from ..ai_tools import Viewer
from ..db import get_db
from ..deps import get_current_user, require_admin
from ..models import AiConversation, AiMessage, AiSettings, User
from ..schemas import (
    DEFAULT_DAILY_QUESTIONS,
    DEFAULT_MAX_TOKENS_PER_CALL,
    AiAskRequest,
    AiConversationCreate,
    AiConversationDetail,
    AiConversationRead,
    AiMessageRead,
    AiSettingsRead,
    AiSettingsUpdate,
    AiStatusRead,
    AiUsageRead,
    AiUsageUser,
)

log = logging.getLogger("producthub.ai")

# 需要登录(第三版);管理员端点在本文件里逐个加 require_admin
router = APIRouter(
    prefix="/api", tags=["ai"], dependencies=[Depends(get_current_user)]
)

DEFAULT_TITLE = "新会话"
_TITLE_CHARS = 30  # 首问取前若干字当标题

# 每日 / 每月的分界线按**北京时间**算,不按 UTC。
# UTC 午夜 = 北京时间早上 8 点 —— 按 UTC 算的话,用户会在早上发现配额莫名其妙重置了。
# 用固定 +8 偏移而**不是** `ZoneInfo("Asia/Shanghai")`:中国没有夏令时,+8 就是准的;
# 而 zoneinfo 在 python:3.10-slim 里可能因为镜像没带 tzdata 而抛
# ZoneInfoNotFoundError —— 那会是一个只在生产上出现、本地永远复现不了的故障。
_CN_TZ = timezone(timedelta(hours=8))


# ---------------------------------------------------------------- 设置

@dataclass(frozen=True)
class EffectiveSettings:
    """管理员设的四个旋钮,**表里没行时用默认值兜底**。

    不让「管理员还没进过这个页面」变成「AI 不能用」—— 表可以是空的。
    """

    enabled: bool
    monthly_token_budget: int | None
    daily_questions_per_user: int
    max_tokens_per_call: int


def _effective_settings(db: Session) -> EffectiveSettings:
    row = db.get(AiSettings, 1)
    if row is None:
        return EffectiveSettings(
            enabled=True,
            monthly_token_budget=None,
            daily_questions_per_user=DEFAULT_DAILY_QUESTIONS,
            max_tokens_per_call=DEFAULT_MAX_TOKENS_PER_CALL,
        )
    return EffectiveSettings(
        enabled=row.enabled,
        monthly_token_budget=row.monthly_token_budget,
        daily_questions_per_user=row.daily_questions_per_user,
        max_tokens_per_call=row.max_tokens_per_call,
    )


# ---------------------------------------------------------------- 用量

def _day_start() -> datetime:
    """北京时间的今天 0 点,换算成 UTC 时刻(库里存的是 timestamptz,比较不受时区影响)。"""
    now = datetime.now(_CN_TZ)
    return now.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)


def _month_start() -> datetime:
    now = datetime.now(_CN_TZ)
    return now.replace(
        day=1, hour=0, minute=0, second=0, microsecond=0
    ).astimezone(timezone.utc)


def _questions_since(db: Session, user_id: int, since: datetime) -> int:
    """某个用户从某时刻起问了几个问题。

    `role == "user"` 那几行才是「一次提问」;助手行按轮次会写多次,数它会重复计。
    """
    return (
        db.scalar(
            select(func.count())
            .select_from(AiMessage)
            .join(AiConversation, AiConversation.id == AiMessage.conversation_id)
            .where(
                AiMessage.role == "user",
                AiConversation.created_by == user_id,
                AiMessage.created_at >= since,
            )
        )
        or 0
    )


def _tokens_since(db: Session, since: datetime) -> int:
    """全局 token 花费(不限用户,月度预算是全局的一个数)。

    `coalesce` 两层都要:一层防某一列是 NULL(失败/中断的行没有用量),
    一层防整个 sum 是 NULL(一条都没有)。少一层就会在「本月第一问之前」
    拿到 None 然后在比较里炸掉。
    """
    return (
        db.scalar(
            select(
                func.coalesce(
                    func.sum(
                        func.coalesce(AiMessage.prompt_tokens, 0)
                        + func.coalesce(AiMessage.completion_tokens, 0)
                    ),
                    0,
                )
            ).where(AiMessage.created_at >= since)
        )
        or 0
    )


# ---------------------------------------------------------------- 会话

def _require_my_conversation(
    db: Session, conversation_id: int, me: User
) -> AiConversation:
    """取一个**我看得见的**会话。

    不存在与「是别人的会话」返回同一个 404 —— 对无权的人来说,它就是不存在。
    同 routers/blog.py 的 `_require_visible`。
    """
    conversation = db.get(AiConversation, conversation_id)
    if conversation is None or not permissions.can_view_ai_conversation(me, conversation):
        raise HTTPException(status_code=404, detail="会话不存在")
    return conversation


def _title_from(content: str) -> str:
    """首问取前若干字当标题。**换行压成空格** —— 标题在侧栏是一行,带换行会撑高。"""
    flat = " ".join(content.split())
    return flat[:_TITLE_CHARS] or DEFAULT_TITLE


@router.get("/ai/status", response_model=AiStatusRead)
def ai_status(
    me: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """当前用户此刻能不能问,以及还能问几问。前端据此把输入框置灰并给出原因。

    **`configured` 与 `enabled` 分开报**:前者是部署状态(有没有 key),
    后者是管理员的开关 —— 要提示的话术和该找的人都不一样。
    """
    settings = _effective_settings(db)
    asked = _questions_since(db, me.id, _day_start())
    used = _tokens_since(db, _month_start())
    budget = settings.monthly_token_budget
    return AiStatusRead(
        configured=ai_client.is_ai_configured(),
        enabled=settings.enabled,
        model=ai_client.model_name(),
        daily_questions_per_user=settings.daily_questions_per_user,
        asked_today=asked,
        remaining_today=max(0, settings.daily_questions_per_user - asked),
        monthly_token_budget=budget,
        month_tokens_used=used,
        month_budget_exceeded=budget is not None and used >= budget,
    )


@router.get("/ai/conversations", response_model=list[AiConversationRead])
def list_conversations(
    me: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """我的会话,最近活动的在前。**只有自己的** —— 管理员也看不到别人的。"""
    rows = db.scalars(
        select(AiConversation)
        .where(AiConversation.created_by == me.id)
        .order_by(AiConversation.updated_at.desc(), AiConversation.id.desc())
    ).all()
    if not rows:
        return []
    counts = dict(
        db.execute(
            select(AiMessage.conversation_id, func.count())
            .where(AiMessage.conversation_id.in_([r.id for r in rows]))
            .group_by(AiMessage.conversation_id)
        ).all()
    )
    return [
        AiConversationRead(
            id=r.id,
            title=r.title,
            message_count=counts.get(r.id, 0),
            created_at=r.created_at,
            updated_at=r.updated_at,
        )
        for r in rows
    ]


@router.post("/ai/conversations", response_model=AiConversationRead, status_code=201)
def create_conversation(
    body: AiConversationCreate,
    me: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """新建一个空会话。标题先给占位,首问之后会被换成问题本身的首句。"""
    conversation = AiConversation(
        title=body.title or DEFAULT_TITLE, created_by=me.id
    )
    db.add(conversation)
    db.commit()
    db.refresh(conversation)
    return AiConversationRead(
        id=conversation.id,
        title=conversation.title,
        message_count=0,
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
    )


@router.get("/ai/conversations/{conversation_id}", response_model=AiConversationDetail)
def get_conversation(
    conversation_id: int,
    me: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    conversation = _require_my_conversation(db, conversation_id, me)
    messages = db.scalars(
        select(AiMessage)
        .where(AiMessage.conversation_id == conversation.id)
        .order_by(AiMessage.id)
    ).all()
    return AiConversationDetail(
        id=conversation.id,
        title=conversation.title,
        message_count=len(messages),
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
        messages=[AiMessageRead.model_validate(m) for m in messages],
    )


@router.delete("/ai/conversations/{conversation_id}", status_code=204)
def delete_conversation(
    conversation_id: int,
    me: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """删会话 —— **硬删**,消息随外键 CASCADE 一起走。

    与评论的「墓碑」不同:那是有别人参与的讨论,删了会抹掉别人的话;
    这里是纯私有的一问一答,主人要删就该真的删掉。
    """
    conversation = _require_my_conversation(db, conversation_id, me)
    db.delete(conversation)
    db.commit()


# ---------------------------------------------------------------- 提问(SSE)

@router.post("/ai/conversations/{conversation_id}/messages")
def ask(
    conversation_id: int,
    body: AiAskRequest,
    me: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """提问,流式返回。★ 文件头那段关于连接池的说明,说的就是这个函数。"""
    conversation = _require_my_conversation(db, conversation_id, me)

    # ---- 1) 开流之前把该拒绝的都拒绝掉 ----
    # 这些必须是**普通 JSON 错误**,不能是 SSE:这时候流还没开始,
    # 前端拿到一个 200 的 text/event-stream 再去里面找错误,是自找的麻烦。
    if not ai_client.is_ai_configured():
        raise HTTPException(
            status_code=503,
            detail=(
                "AI 未配置:请在环境变量配置 DEEPSEEK_API_KEY"
                "(照邮件那套,没配就是不能用的状态,不是报错)"
            ),
        )

    settings = _effective_settings(db)

    if not settings.enabled:
        raise HTTPException(status_code=403, detail="AI 助手已被管理员关闭")

    asked = _questions_since(db, me.id, _day_start())
    if asked >= settings.daily_questions_per_user:
        raise HTTPException(
            status_code=429,
            detail=(
                f"今日提问次数已用完({asked}/{settings.daily_questions_per_user}),"
                "每天 0 点(北京时间)重置"
            ),
        )

    budget = settings.monthly_token_budget
    used = _tokens_since(db, _month_start())
    if budget is not None and used >= budget:
        raise HTTPException(
            status_code=429,
            detail=(
                f"本月 AI 额度已用完(已用 {used} / 上限 {budget} tokens),"
                "请联系管理员调整"
            ),
        )

    # ---- 2) 一个短事务:落提问、落占位回答、首问换标题 ----
    title: str | None = None
    if conversation.title == DEFAULT_TITLE:
        title = _title_from(body.content)
        conversation.title = title

    db.add(
        AiMessage(
            conversation_id=conversation.id,
            role="user",
            content=body.content,
            # 用户消息写下来就是完整的;`running` 是助手行才有的状态。
            status="done",
        )
    )
    # **先落一条 running 的占位行、结束时改写**:这样流中断(关标签页 / 断网 /
    # 进程重启)也留痕,不会静默丢一次提问 —— 而钱是用户在出的。
    answer = AiMessage(
        conversation_id=conversation.id, role="assistant", content="", status="running"
    )
    db.add(answer)
    db.commit()

    # ---- 3) 取快照、然后**把连接还给池子** ----
    # 顺序不能反:close() 之后对象是 detached 的,读 id/role/name 恰好还能用,
    # 但那是一个「碰巧成立」的假设,不要建立在它上面。
    answer_id = answer.id
    viewer = Viewer.of(me)
    db.close()

    def event_stream():
        """把 AI 的事件序列包成 SSE。

        这里**不碰 `db`**(它已经关了),落库由 ai_agent 自己开短命会话完成。
        JSON 里不会有换行 —— `json.dumps` 会把正文里的 `\\n` 转义掉,
        所以一个事件就是一行 `data:`,不需要按多行 data 处理。
        """
        try:
            for event in ai_agent.stream_answer(
                conversation_id=conversation_id,
                assistant_message_id=answer_id,
                viewer=viewer,
                title=title,
                max_tokens=settings.max_tokens_per_call,
            ):
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        except Exception:  # noqa: BLE001 —— 生成器里抛出去只会变成一条断掉的连接
            log.exception("AI SSE 流异常终止(conversation_id=%s)", conversation_id)
            payload = {"type": "error", "detail": "服务端出错,本次回答已中断"}
            yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            # 不缓存:SSE 被缓存住就完全不是流了
            "Cache-Control": "no-cache",
            # 防 nginx / Railway 边缘代理把响应缓冲起来 —— 缓冲掉的话,
            # 用户要等整段生成完才看到字,流式就白做了。
            # ⚠️ 这一个头**本地测不出来**(没有代理),上线后必须手工确认。
            "X-Accel-Buffering": "no",
        },
    )


# ---------------------------------------------------------------- 管理员:设置

def _to_settings_read(settings: EffectiveSettings, row: AiSettings | None) -> AiSettingsRead:
    return AiSettingsRead(
        enabled=settings.enabled,
        monthly_token_budget=settings.monthly_token_budget,
        daily_questions_per_user=settings.daily_questions_per_user,
        max_tokens_per_call=settings.max_tokens_per_call,
        updated_at=row.updated_at if row is not None else None,
    )


@router.get("/ai/settings", response_model=AiSettingsRead)
def get_ai_settings(
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """读限额设置(管理员)。表里没行时返回**默认值**,不是 404 ——
    前端拿到的永远是一份可以直接渲染的设置。"""
    return _to_settings_read(_effective_settings(db), db.get(AiSettings, 1))


@router.put("/ai/settings", response_model=AiSettingsRead)
def update_ai_settings(
    body: AiSettingsUpdate,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """改限额设置(管理员)。**全量提交**,四个旋钮一起写。

    第一次调用时表里没有行,这里补一条 —— 「管理员第一次打开这个页面」就是它被创建的时机,
    不需要迁移里预置一行(那样反而多一个「行是空的还是被删了」的状态要判)。
    """
    row = db.get(AiSettings, 1)
    if row is None:
        row = AiSettings(id=1)
        db.add(row)

    row.enabled = body.enabled
    row.monthly_token_budget = body.monthly_token_budget
    row.daily_questions_per_user = body.daily_questions_per_user
    row.max_tokens_per_call = body.max_tokens_per_call
    row.updated_by = admin.id
    db.commit()
    db.refresh(row)
    return _to_settings_read(_effective_settings(db), row)


@router.get("/ai/usage", response_model=AiUsageRead)
def get_ai_usage(
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """本月用量 + 今日各人提问数(管理员)。

    **只有数字,没有任何提问内容** —— 这是 `can_view_ai_conversation` 与
    `can_manage_ai_settings` 那条分界线的落点:管理员管的是「额度多少」这个旋钮,
    不是「别人问了什么」。查出「谁在烧钱」和「他到底问了什么」是两件事。
    """
    settings = _effective_settings(db)

    month_rows = dict(
        db.execute(
            select(
                AiConversation.created_by,
                func.coalesce(
                    func.sum(
                        func.coalesce(AiMessage.prompt_tokens, 0)
                        + func.coalesce(AiMessage.completion_tokens, 0)
                    ),
                    0,
                ),
            )
            .join(AiMessage, AiMessage.conversation_id == AiConversation.id)
            .where(AiMessage.created_at >= _month_start())
            .group_by(AiConversation.created_by)
        ).all()
    )
    today_rows = dict(
        db.execute(
            select(AiConversation.created_by, func.count())
            .join(AiMessage, AiMessage.conversation_id == AiConversation.id)
            .where(
                AiMessage.role == "user",
                AiMessage.created_at >= _day_start(),
            )
            .group_by(AiConversation.created_by)
        ).all()
    )

    # 有花费或有提问的人都要出现在表里(两个集合的并集)—— 只看其中一个的话,
    # 「今天问了但本月还没花钱」的人会凭空消失。
    user_ids = {i for i in (*month_rows, *today_rows) if i is not None}
    names = (
        dict(db.execute(select(User.id, User.name).where(User.id.in_(user_ids))).all())
        if user_ids
        else {}
    )

    users = [
        AiUsageUser(
            user_id=user_id,
            # 账号被删了(auth 里 CASCADE 会连会话一起删,所以基本不会出现),
            # 名字取不到就给个能看懂的占位,别让整个报表 500
            name=names.get(user_id, "（已删除的账号）"),
            questions_today=today_rows.get(user_id, 0),
            tokens_this_month=month_rows.get(user_id, 0),
        )
        for user_id in user_ids
    ]
    users.sort(key=lambda u: (-u.tokens_this_month, -u.questions_today, u.name))

    return AiUsageRead(
        month_tokens_used=_tokens_since(db, _month_start()),
        monthly_token_budget=settings.monthly_token_budget,
        daily_questions_per_user=settings.daily_questions_per_user,
        users=users,
    )
