"""CRUD 里逐字重复的那几行(第四版)。

抽的是**行**,不是端点 —— 每个资源的五个端点仍旧在自己的 router 文件里显式写全。
产品和分类的端点看着像,其实只有中间这几行是逐字相同的;往外一圈就各不相同了
(产品要预加载三张子表、要手工组装响应、要校验分类 id)。所以这里只有几个纯函数,
不继承、不泛型;读某个 router 时不用跳来跳去理解框架。

record_event 也放这里:它是写路径上唯一一处「顺手记一笔」的动作,helper 收在这儿,
免得每个写端点各拼一遍字段。
"""
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .content_types import content_type_of, title_of
from .models import ActivityEvent, User
from .schemas import EventAction

# 动作取值,存进 activity_events.action;类型取自 schemas(那是接口契约的出处),
# 这里只是给写端点的代码三个好念的名字。
ACTION_CREATE: EventAction = "create"
ACTION_UPDATE: EventAction = "update"
ACTION_DELETE: EventAction = "delete"


def get_or_404(db: Session, model, obj_id: int, label: str):
    """按主键取一条,不存在就 404「{label}不存在」。"""
    obj = db.get(model, obj_id)
    if obj is None:
        raise HTTPException(status_code=404, detail=f"{label}不存在")
    return obj


def apply_patch(obj, payload: dict, fields: list[str] | None = None) -> None:
    """把请求体里真正出现的字段写到对象上(PATCH 语义:缺席即不改)。

    payload 是 ``body.model_dump(exclude_unset=True)``。fields 给白名单时只认名单里的
    键(products 用它挡住 category_ids / price_tiers 这类要单独处理的子集合);
    categories 是整表字段都可改,传 None 即可。
    """
    for field, value in payload.items():
        if fields is None or field in fields:
            setattr(obj, field, value)


def commit_or_409(db: Session, detail: str) -> None:
    """提交;唯一约束冲突报 409。

    冲突时**必须回滚** —— 不回滚的话这个会话会带着失败的写操作留给下一个请求。
    """
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail=detail)


def record_event(db: Session, actor: User | None, action: str, obj) -> None:
    """记一条动态,**不提交**(跟着调用方的事务一起走)。

    必须在 commit 之前调用:删除场景下对象随事务消失,提交后再读它的字段就取不到了。
    标题存**快照** —— 对象以后会被删,动态流不能因此变成空白。

    内容类型由 obj 自己推出来(Product → product),不用调用方再传一遍:
    两个东西就有对不上的一天,而对不上不会报错,只会记成别的类型。
    """
    ct = content_type_of(obj)
    db.add(
        ActivityEvent(
            actor_id=actor.id if actor is not None else None,
            actor_name=actor.name if actor is not None else "系统",
            action=action,
            content_type=ct.key,
            object_id=obj.id,
            title=title_of(obj, ct),
        )
    )
