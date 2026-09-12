"""评论与点赞(第五版)—— 产品 / 需求 / 以后的博客共用。

路由挂 /api 前缀:
  GET    /api/comments?target_type=&target_id=   某对象的两级评论树
  POST   /api/comments                           发评论或回复
  DELETE /api/comments/{id}                      删(其实是**墓碑**,见下)
  POST   /api/comments/{id}/like                 点赞(幂等)
  DELETE /api/comments/{id}/like                 取消点赞(幂等)

**多态指针**:`target_type` 的合法取值来自 `app/content_types.py` 的 BY_KEY,
不在这份文件里另写一份清单 —— 「有哪些内容类型」只能有一个出处,否则加内容类型时
漏改一处不会报错,只会静默地不让评论。目标存在性用 `ct.model` + `db.get` 查,
**没有 FK 能替我们做这件事**。

**删除是墓碑**:正文清空、行留着,所以它下面的回复不会跟着消失。硬删一条顶层评论会
连带删掉别人写的回复 —— 一个人可以抹掉一整段自己没参与的讨论。

**不记动态、不登记内容类型**:照「分类不记」的先例 —— 评论是附着在内容上的互动,
记了会把首页动态冲成流水账。
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import crud, permissions
from ..content_types import BY_KEY
from ..db import get_db
from ..deps import get_current_user, now_utc
from ..models import Comment, CommentLike, User
from ..schemas import CommentCreate, CommentRead

# 需要登录(第三版);业务访问还受 must_change_password 硬门禁约束
router = APIRouter(
    prefix="/api", tags=["comments"], dependencies=[Depends(get_current_user)]
)


def _require_target(db: Session, target_type: str, target_id: int) -> None:
    """校验评论对象存在 —— comments 上没有 FK,数据库不会替我们挡。"""
    ct = BY_KEY.get(target_type)
    if ct is None or not ct.enabled:
        raise HTTPException(status_code=404, detail="内容类型不存在")
    if db.get(ct.model, target_id) is None:
        raise HTTPException(status_code=404, detail="评论对象不存在")


def _require_not_deleted(comment: Comment, action: str) -> None:
    """墓碑评论是个「已删除」的占位,不能再被回复或点赞。"""
    if comment.deleted_at is not None:
        raise HTTPException(status_code=422, detail=f"该评论已删除,不能再{action}")


def _current_names(db: Session, comments: list[Comment]) -> dict[int, str]:
    """按 author_id 取当前姓名。

    评论是**活的内容**,用户改名后应当跟着改 —— 所以这里查当前姓名,
    而不是直接用写入时记下的 author_name(那一列只在账号被删之后兜底)。
    """
    ids = {c.author_id for c in comments if c.author_id is not None}
    if not ids:
        return {}
    return dict(db.execute(select(User.id, User.name).where(User.id.in_(ids))).all())


def _build(comments: list[Comment], me: User, db: Session) -> list[CommentRead]:
    """把一批评论组装成 `CommentRead`(不排层级,层级由调用方决定)。

    点赞数用**一条 GROUP BY**、我赞没赞用**一条 IN 查询**,在 Python 里并进结果。
    逐条查的话,50 条评论的线程会变成 100 次查询。
    """
    ids = [c.id for c in comments]
    if not ids:
        return []
    counts = dict(
        db.execute(
            select(CommentLike.comment_id, func.count())
            .where(CommentLike.comment_id.in_(ids))
            .group_by(CommentLike.comment_id)
        ).all()
    )
    mine = set(
        db.scalars(
            select(CommentLike.comment_id).where(
                CommentLike.comment_id.in_(ids),
                CommentLike.user_id == me.id,
            )
        ).all()
    )
    names = _current_names(db, comments)
    return [
        CommentRead(
            id=c.id,
            target_type=c.target_type,
            target_id=c.target_id,
            author_id=c.author_id,
            # 账号还在就用当前姓名;被删了(author_id 为空)才回落到写入时的快照
            author_name=names.get(c.author_id) or c.author_name,
            body=c.body,
            parent_id=c.parent_id,
            deleted_at=c.deleted_at,
            like_count=counts.get(c.id, 0),
            liked_by_me=c.id in mine,
            replies=[],
            created_at=c.created_at,
        )
        for c in comments
    ]


def _to_read(db: Session, comment: Comment, me: User) -> CommentRead:
    """单条 —— POST 的响应走这里。

    单条也可能是**回复**(parent_id 非空),所以不能走 `_to_tree`(那个只回顶层)。
    """
    return _build([comment], me, db)[0]


def _to_tree(db: Session, comments: list[Comment], me: User) -> list[CommentRead]:
    """把顶层 + 回复组装成两级树。回复按 parent_id 挂回去,不在顶层重复出现。"""
    rows = _build(comments, me, db)

    replies: dict[int, list[CommentRead]] = {}
    roots: list[CommentRead] = []
    for row in rows:
        if row.parent_id is None:
            roots.append(row)
        else:
            replies.setdefault(row.parent_id, []).append(row)
    for root in roots:
        root.replies = replies.get(root.id, [])
    return roots


@router.get("/comments", response_model=list[CommentRead])
def list_comments(
    target_type: str,
    target_id: int,
    limit: int = Query(default=200, ge=1, le=500),
    me: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """某对象的评论树:顶层按时间正序,回复嵌在各自的顶层评论里。

    limit 限制的是**顶层评论**条数(回复跟着它们的父评论一起返回)。全站都没有分页,
    但评论是唯一可能被人一口气灌几百条的地方,所以这里给个上限兜住响应大小。
    """
    _require_target(db, target_type, target_id)

    roots = db.scalars(
        select(Comment)
        .where(
            Comment.target_type == target_type,
            Comment.target_id == target_id,
            Comment.parent_id.is_(None),
        )
        .order_by(Comment.created_at, Comment.id)
        .limit(limit)
    ).all()
    if not roots:
        return []

    replies = db.scalars(
        select(Comment)
        .where(Comment.parent_id.in_([c.id for c in roots]))
        .order_by(Comment.created_at, Comment.id)
    ).all()
    return _to_tree(db, [*roots, *replies], me)


@router.post("/comments", response_model=CommentRead, status_code=201)
def create_comment(
    body: CommentCreate,
    me: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """发一条评论,或回复一条顶层评论(回复**不能**再被回复)。"""
    _require_target(db, body.target_type, body.target_id)

    if body.parent_id is not None:
        parent = db.get(Comment, body.parent_id)
        # 父评论必须存在、挂在同一个对象上、而且自己就是顶层 —— 三条缺一不可。
        # 不校验「同一个对象」的话,回复可以挂到别的产品的评论下面,读的时候却按
        # target 过滤,那条回复就再也看不见了。
        if (
            parent is None
            or parent.target_type != body.target_type
            or parent.target_id != body.target_id
        ):
            raise HTTPException(status_code=422, detail="回复的评论不存在")
        if parent.parent_id is not None:
            raise HTTPException(status_code=422, detail="回复不支持再回复")
        _require_not_deleted(parent, "回复")

    comment = Comment(
        target_type=body.target_type,
        target_id=body.target_id,
        author_id=me.id,
        author_name=me.name,
        body=body.body,
        parent_id=body.parent_id,
    )
    db.add(comment)
    db.commit()
    db.refresh(comment)
    return _to_read(db, comment, me)


@router.delete("/comments/{comment_id}", status_code=204)
def delete_comment(
    comment_id: int,
    me: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """删评论 —— 作者本人或管理员(见 app/permissions.py)。

    这是**墓碑**不是硬删:正文清空、行留着,所以别人写的回复不会跟着消失。
    重复删同一条不报错(已经是墓碑了,再删一次什么也不发生)。
    """
    comment = crud.get_or_404(db, Comment, comment_id, "评论")
    if not permissions.can_delete_comment(me, comment):
        raise HTTPException(status_code=403, detail="只能删除自己的评论")
    if comment.deleted_at is None:
        comment.body = ""  # 内容是真的删掉了,管理员的删违规诉求照样满足
        comment.deleted_at = now_utc()
        db.commit()


@router.post("/comments/{comment_id}/like", status_code=204)
def like_comment(
    comment_id: int,
    me: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """点赞。**幂等**:已经赞过再赞一次仍是 204(复合主键挡住重复行)。"""
    comment = crud.get_or_404(db, Comment, comment_id, "评论")
    _require_not_deleted(comment, "点赞")
    db.add(CommentLike(comment_id=comment.id, user_id=me.id))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()  # 已经赞过了,这就是想要的终态


@router.delete("/comments/{comment_id}/like", status_code=204)
def unlike_comment(
    comment_id: int,
    me: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """取消点赞。**幂等**:没赞过也返回 204。"""
    crud.get_or_404(db, Comment, comment_id, "评论")
    db.execute(
        delete(CommentLike).where(
            CommentLike.comment_id == comment_id, CommentLike.user_id == me.id
        )
    )
    db.commit()
