"""博客(BlogPost)CRUD + 标签 + 点赞 —— 第五版。

路由挂 /api 前缀:
  GET    /api/blog                  列表(已发布的 + 自己的草稿),可选 ?tag_id=
  POST   /api/blog                  新建(默认草稿,直接带 status=published 就是写完就发)
  GET    /api/blog/{id}             详情
  PATCH  /api/blog/{id}             部分更新(含**首次发布**)
  DELETE /api/blog/{id}             删除
  POST / DELETE /api/blog/{id}/like 点赞 / 取消(幂等)
  GET    /api/blog/tags             标签列表
  POST   /api/blog/tags             新建标签
  PATCH  /api/blog/tags/{id}        改标签名
  DELETE /api/blog/tags/{id}        删除标签

⚠️ **顺序**:`/blog/tags` 那几条必须写在 `/blog/{post_id}` **前面**。FastAPI 按声明顺序
匹配,`{post_id}` 在前的话 `/blog/tags` 会先落到它头上,拿 "tags" 去转 int 得 422。
tests/test_blog.py 里有标签用例盯着这件事(标签接口 422 了就是顺序被挪了)。

**草稿**:这一版第一次出现「行存在 ≠ 可见」。列表里看不到别人的草稿,详情对无权的人
**404 而不是 403** —— 403 等于承认「有这么一篇」,那本身就不该漏。汇总计数
(routers/home.py)与评论接口(routers/comments.py)也都不认它,两处都读
content_types 里的 published_field。

**评论**复用第五版 5B 那套(CommentThread 组件 + /api/comments),这里只需要在删除时
替多态指针结账(crud.delete_comments_for)。

**动态规则**(草稿要是在动态里广播,等于把没写完的东西喊给所有人):
  - 草稿的新建 / 编辑 / 删除 —— **都不记**;
  - **首次发布**记一条 create(它此刻才出现在大家面前);
  - 已发布的帖子再编辑记 update,删除记 delete。
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import delete, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from .. import crud, permissions
from ..db import get_db
from ..deps import get_current_user, now_utc
from ..models import BlogPost, BlogPostTag, BlogTag, PostLike, User
from ..schemas import (
    BlogPostCreate,
    BlogPostRead,
    BlogPostUpdate,
    BlogTagCreate,
    BlogTagRead,
    BlogTagUpdate,
)

# 需要登录(第三版);业务访问还受 must_change_password 硬门禁约束
router = APIRouter(
    prefix="/api", tags=["blog"], dependencies=[Depends(get_current_user)]
)

_PUBLISHED: str = "published"


def _load_post(db: Session, post_id: int) -> BlogPost | None:
    """带标签关联一次性取出一篇帖子,避免懒加载。

    `populate_existing`:让这次查询**覆盖**会话里那个对象的字段,而不是因为它在
    identity map 里就原样返回。原因是 `published_at` 由 Python 侧 now_utc() 写进去,
    不覆盖的话,刚发布那一次的响应里它会被序列化成 `...Z`,而之后每次读回来都是
    数据库渲染的 `+08:00` —— 同一个时刻两种写法,写测试的人迟早会被它绊一下。
    """
    return db.scalars(
        select(BlogPost)
        .where(BlogPost.id == post_id)
        .options(selectinload(BlogPost.tag_links).selectinload(BlogPostTag.tag))
        .execution_options(populate_existing=True)
    ).first()


def _require_visible(db: Session, post_id: int, me: User) -> BlogPost:
    """取一篇**看得见的**帖子。

    不存在与「是别人的草稿」返回同一个 404:对没有权限的人来说,草稿就是不存在的。
    看得见但不能改(别人的已发布帖子)由调用方再判一道,那种情况 403 是合适的 ——
    帖子本来就在列表里摆着,承认它存在不泄露任何东西。
    """
    post = _load_post(db, post_id)
    if post is None or not permissions.can_view_post(me, post):
        raise HTTPException(status_code=404, detail="帖子不存在")
    return post


def _author_names(db: Session, posts: list[BlogPost]) -> dict[int, str]:
    """按 author_id 取当前姓名。

    帖子是**活的内容**,用户改名后应当跟着改;`author_name` 那一列只在账号被删
    (`author_id` 为空)之后兜底。同评论的 _current_names。
    """
    ids = {p.author_id for p in posts if p.author_id is not None}
    if not ids:
        return {}
    return dict(db.execute(select(User.id, User.name).where(User.id.in_(ids))).all())


def _build(db: Session, posts: list[BlogPost], me: User) -> list[BlogPostRead]:
    """把一批帖子组装成响应(标签按 tag_id、点赞数一条 GROUP BY、我赞没赞一条 IN)。"""
    ids = [p.id for p in posts]
    if not ids:
        return []
    counts = dict(
        db.execute(
            select(PostLike.post_id, func.count())
            .where(PostLike.post_id.in_(ids))
            .group_by(PostLike.post_id)
        ).all()
    )
    mine = set(
        db.scalars(
            select(PostLike.post_id).where(
                PostLike.post_id.in_(ids), PostLike.user_id == me.id
            )
        ).all()
    )
    names = _author_names(db, posts)
    return [
        BlogPostRead(
            id=p.id,
            title=p.title,
            body=p.body,
            # 派生字段:published_at 是「发布状态」的唯一出处(见 models.BlogPost)
            status=_PUBLISHED if p.published_at is not None else "draft",
            published_at=p.published_at,
            author_id=p.author_id,
            author_name=names.get(p.author_id) or p.author_name,
            tags=[
                BlogTagRead.model_validate(link.tag)
                for link in sorted(p.tag_links, key=lambda link: link.tag_id)
            ],
            like_count=counts.get(p.id, 0),
            liked_by_me=p.id in mine,
            created_at=p.created_at,
            updated_at=p.updated_at,
        )
        for p in posts
    ]


def _require_tags_exist(db: Session, ids: list[int]) -> list[int]:
    """校验标签 id 都存在,缺失则 400;返回去重排序后的 id。同产品的分类。"""
    unique = sorted(set(ids))
    existing = set(db.scalars(select(BlogTag.id).where(BlogTag.id.in_(unique))).all())
    missing = set(unique) - existing
    if missing:
        raise HTTPException(status_code=400, detail=f"标签不存在: {sorted(missing)}")
    return unique


def _set_tag_links(db: Session, post: BlogPost, tag_ids: list[int]) -> None:
    """把帖子的标签设成这一组(多的删、少的加)。

    先清空旧关联(delete-orphan 会删掉旧行)再打新标,中间 flush 一次,
    免得新旧行撞复合主键。新建时旧关联本来就是空的,走的是同一段代码。
    """
    ids = _require_tags_exist(db, tag_ids)
    post.tag_links.clear()
    db.flush()
    for tag_id in ids:
        post.tag_links.append(BlogPostTag(post_id=post.id, tag_id=tag_id))


# ---------- 标签 ----------
# 全部写在 /blog/{post_id} 前面,理由见文件头那条 ⚠️。
# 标签**不进动态**:它是标签不是内容,记了只会把动态稀释成「新建标签」流水账(同分类)。


@router.get("/blog/tags", response_model=list[BlogTagRead])
def list_tags(db: Session = Depends(get_db)):
    return db.scalars(select(BlogTag).order_by(BlogTag.id)).all()


@router.post("/blog/tags", response_model=BlogTagRead, status_code=201)
def create_tag(body: BlogTagCreate, db: Session = Depends(get_db)):
    tag = BlogTag(name=body.name)
    db.add(tag)
    crud.commit_or_409(db, "标签名已存在")  # 唯一约束冲突:name 重复
    db.refresh(tag)
    return tag


@router.patch("/blog/tags/{tag_id}", response_model=BlogTagRead)
def update_tag(tag_id: int, body: BlogTagUpdate, db: Session = Depends(get_db)):
    tag = crud.get_or_404(db, BlogTag, tag_id, "标签")
    crud.apply_patch(tag, body.model_dump(exclude_unset=True))
    crud.commit_or_409(db, "标签名已存在")
    db.refresh(tag)
    return tag


@router.delete("/blog/tags/{tag_id}", status_code=204)
def delete_tag(tag_id: int, db: Session = Depends(get_db)):
    tag = crud.get_or_404(db, BlogTag, tag_id, "标签")
    db.delete(tag)  # 帖子本身保留,只是摘掉这个标签(关联行由 CASCADE 带走)
    db.commit()


# ---------- 帖子 ----------


@router.get("/blog", response_model=list[BlogPostRead])
def list_posts(
    tag_id: int | None = None,
    me: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """列表:已发布的 + **自己的**草稿(管理员看得到全部草稿)。

    排序按「它出现的时间」倒序 —— 已发布的按发布时间,草稿按创建时间
    (coalesce 一下):草稿在创建那天出现,发布那天重新出现,一条规则说得通。

    搜索与状态筛选放在前端做(帖子量级在千级以内),同需求列表;后端只留按标签筛,
    因为那是唯一一个「查不到就少一条」的筛选。
    """
    stmt = select(BlogPost).options(
        selectinload(BlogPost.tag_links).selectinload(BlogPostTag.tag)
    )
    if tag_id is not None:
        stmt = stmt.where(BlogPost.tag_links.any(BlogPostTag.tag_id == tag_id))
    if me.role != "admin":
        # 别人的草稿压根不该出现在列表里 —— 这是「草稿只有作者与管理员可见」的过滤端
        stmt = stmt.where(
            or_(BlogPost.published_at.isnot(None), BlogPost.author_id == me.id)
        )
    stmt = stmt.order_by(
        func.coalesce(BlogPost.published_at, BlogPost.created_at).desc(),
        BlogPost.id.desc(),
    )
    return _build(db, db.scalars(stmt).all(), me)


@router.post("/blog", response_model=BlogPostRead, status_code=201)
def create_post(
    body: BlogPostCreate,
    me: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """新建。默认存草稿;带 `status="published"` 就是写完就发。"""
    post = BlogPost(
        title=body.title,
        body=body.body,
        author_id=me.id,
        author_name=me.name,  # 兜底署名:账号被删之后还认得出是谁写的
        published_at=now_utc() if body.status == _PUBLISHED else None,
    )
    db.add(post)
    db.flush()  # 先拿到 post.id,打标签与记动态都要用

    _set_tag_links(db, post, body.tag_ids)

    # 草稿不记:它还没打算给人看,记了等于把没写完的东西广播出去
    if post.published_at is not None:
        crud.record_event(db, me, crud.ACTION_CREATE, post)

    db.commit()
    return _build(db, [_load_post(db, post.id)], me)[0]


@router.get("/blog/{post_id}", response_model=BlogPostRead)
def get_post(
    post_id: int,
    me: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return _build(db, [_require_visible(db, post_id, me)], me)[0]


@router.patch("/blog/{post_id}", response_model=BlogPostRead)
def update_post(
    post_id: int,
    body: BlogPostUpdate,
    me: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """部分更新;`status="published"` 走**首次发布**。"""
    post = _require_visible(db, post_id, me)
    if not permissions.can_edit_post(me, post):
        raise HTTPException(status_code=403, detail="只能编辑自己的帖子")

    payload = body.model_dump(exclude_unset=True)

    # 先判后改:退不回草稿这条要在动任何字段之前挡住。
    # 「发布时间」是一个已经发生的事实,退回去就得把它抹掉 —— 真要撤回发布,那是删除的事。
    if payload.get("status") == "draft" and post.published_at is not None:
        raise HTTPException(status_code=422, detail="已发布的帖子不能退回草稿")

    was_published = post.published_at is not None

    # 标量字段进 setattr;tag_ids 是子集合,整组替换(「键在不在」看 payload)
    crud.apply_patch(post, payload, fields=["title", "body"])
    if "tag_ids" in payload:
        _set_tag_links(db, post, body.tag_ids or [])

    if payload.get("status") == _PUBLISHED and not was_published:
        post.published_at = now_utc()  # 首次发布:只写这一次,以后编辑不动它

    if post.published_at is not None:
        # 这一改把它推上线了 → 记「新建」(它此刻才出现在大家面前);
        # 本来就发布过的再改 → 记「更新」。草稿的编辑一条都不记。
        crud.record_event(
            db,
            me,
            crud.ACTION_UPDATE if was_published else crud.ACTION_CREATE,
            post,
        )

    db.commit()
    return _build(db, [_load_post(db, post.id)], me)[0]


@router.delete("/blog/{post_id}", status_code=204)
def delete_post(
    post_id: int,
    me: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    post = _require_visible(db, post_id, me)
    if not permissions.can_edit_post(me, post):
        raise HTTPException(status_code=403, detail="只能删除自己的帖子")

    if post.published_at is not None:
        # 先记动态再删:提交之后就读不到它的标题了。删草稿不记 —— 它从没露过面。
        crud.record_event(db, me, crud.ACTION_DELETE, post)
    # comments 上没有指向帖子的 FK,多态指针的账单在这里手动结(见 crud.delete_comments_for)
    crud.delete_comments_for(db, post)
    db.delete(post)
    db.commit()


@router.post("/blog/{post_id}/like", status_code=204)
def like_post(
    post_id: int,
    me: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """点赞。**幂等**:已经赞过再赞一次仍是 204(复合主键挡住重复行)。"""
    post = _require_visible(db, post_id, me)
    db.add(PostLike(post_id=post.id, user_id=me.id))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()  # 已经赞过了,这就是想要的终态


@router.delete("/blog/{post_id}/like", status_code=204)
def unlike_post(
    post_id: int,
    me: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """取消点赞。**幂等**:没赞过也返回 204。"""
    post = _require_visible(db, post_id, me)
    db.execute(
        delete(PostLike).where(
            PostLike.post_id == post.id, PostLike.user_id == me.id
        )
    )
    db.commit()
