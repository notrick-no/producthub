"""首页要的两块数据(第四版):

  GET /api/activity?limit=&offset=   最近动态(按时间倒序)
  GET /api/summary                   项目汇总(每种内容类型现有多少条)

两块都从 app/content_types.py 那份清单派生 —— 汇总遍历它做计数,动态按它把
content_type 翻成中文名、按它的 path 拼前端链接。所以以后加会议/博客,
**这个文件一行都不用改**,前端也一行都不用改。
"""
from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..content_types import BY_KEY, CONTENT_TYPES
from ..db import get_db
from ..deps import get_current_user
from ..models import ActivityEvent
from ..schemas import ActivityEventRead, SummaryItem

# 需要登录(第三版);业务访问还受 must_change_password 硬门禁约束
router = APIRouter(
    prefix="/api", tags=["home"], dependencies=[Depends(get_current_user)]
)


def _name_of(content_type: str) -> str:
    """内容类型的中文名。

    清单里已经没有的旧类型就原样显示 key —— 宁可首页上出现一个英文词,
    也别让整个动态流 500。
    """
    ct = BY_KEY.get(content_type)
    return ct.name if ct is not None else content_type


def _url_of(content_type: str, action: str, object_id: int) -> str | None:
    """把事件拼成前端路由。

    **删除事件返回 None**:对象已经没了,给个链接只会让用户点出 404;
    前端看 url 是不是 None 决定渲染成链接还是纯文本。
    """
    if action == "delete":
        return None
    ct = BY_KEY.get(content_type)
    return f"{ct.path}/{object_id}" if ct is not None else None


@router.get("/activity", response_model=list[ActivityEventRead])
def list_activity(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    """最近动态。

    created_at 相同(同一秒内连着操作)时按 id 倒序兜底,保证分页顺序稳定。
    """
    events = db.scalars(
        select(ActivityEvent)
        .order_by(ActivityEvent.created_at.desc(), ActivityEvent.id.desc())
        .limit(limit)
        .offset(offset)
    ).all()

    return [
        ActivityEventRead(
            id=e.id,
            actor_name=e.actor_name,
            action=e.action,
            content_type=e.content_type,
            content_type_name=_name_of(e.content_type),
            title=e.title,
            object_id=e.object_id,
            url=_url_of(e.content_type, e.action, e.object_id),
            created_at=e.created_at,
        )
        for e in events
    ]


@router.get("/summary", response_model=list[SummaryItem])
def get_summary(db: Session = Depends(get_db)):
    """项目汇总:每种内容类型的现有条数。

    只返回 enabled 的类型 —— 本版没做的(会议、博客)不返回,前端就不会显示
    一张永远是 0 的卡。以后做出来了,在 content_types.py 里打开 enabled 即可。
    """
    items: list[SummaryItem] = []
    for ct in CONTENT_TYPES:
        if not ct.enabled:
            continue
        count = db.scalar(select(func.count()).select_from(ct.model)) or 0
        items.append(SummaryItem(key=ct.key, name=ct.name, count=count, url=ct.path))
    return items
