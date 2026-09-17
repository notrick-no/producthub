"""AI 能调用的工具(第六版)—— **全部只读**。

## 只读不是「这一版先这样」,是安全边界本身

产品的简介、帖子正文、评论都是**用户写的自由文本**,会作为工具结果进模型上下文,
所以「提示注入」在这套系统里是必然存在的,不是假设。之所以不构成问题,是因为
**工具只读、且只读已发布** —— 注入能拿到的数据,本来就对所有人可见。
爆炸半径为零。将来一旦加入任何**可写**工具(发帖、改状态、发信),这条推理立刻失效,
那时必须重新做威胁建模,而不是继续沿用「反正只读」。

## 三条真实的泄露路径(实现时逐条防)

1. **`db.get(BlogPost, id)` 或裸 `select(BlogPost)`** —— 最可能的 bug。十个工具里
   九个不需要过滤,过滤最容易正好漏在那一个上。对策:博客的两个工具**必须**经过
   `_published_only()`,而它的名字取得让「漏了」在 review 时一眼可见。
2. **搜索帖子的 `body`** —— `ilike` 打在正文上,会把草稿…不,是会把整篇正文连片段
   一起送进上下文。所以**搜索只搜 `title`**;正文只在 `get_post` 里整篇取(且已发布)。
3. **误用 `permissions.can_view_post`** —— 那个谓词带的是「作者与管理员可见草稿」的逻辑,
   是**界面**的规则。AI 的规则比它严(见下)。

## 一句必须说准的话

**不能说「AI 永远不会泄露别人的草稿」。** 管理员本来就看得到所有人的草稿
(`routers/blog.py:222`、`permissions.py:40`)。正确的不变式是:
**「AI 读到的每一行都是已发布的」** —— 这比界面对管理员还严一档。

代价要认:**管理员在界面上看得到草稿,问 AI 却说「没有」**。这是有意的,
`doc/架构.md` 里写明了,测试也钉住了(断言草稿标题不出现在**发出的请求体**里)。

## 形状约定

工具函数统一签名 `(db, *, viewer, **kwargs) -> dict`(返回可直接 JSON 化的字典)。

`viewer` **这一版没有任何工具真的用它** —— 因为规则是「已发布」而不是「我能不能看」。
保留它是为了:① 将来出现真正按人区分的工具(比如「我的草稿」)时签名不用改;
② 它本身就是那条不变式的见证 —— **AI 的可见性与提问者是谁无关**。
⚠️ 但**不要**拿它去做可见性过滤:那正是第 3 条泄露路径。
"""
import json
import logging
from dataclasses import dataclass
from typing import Any, Callable

from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from .models import (
    BlogPost,
    BlogPostTag,
    BlogTag,
    Category,
    Product,
    ProductCategory,
    ProductPriceTier,
    Requirement,
    User,
)
from .routers import home

log = logging.getLogger("producthub.ai")

# ---------------------------------------------------------------- 取数上限
#
# 全站列表端点多数**返回整张表**(list_products / list_categories / …全部 .all() 无上限),
# 那个选择对界面成立(千级数据、前端自己筛选),但**对模型不成立**:工具结果是直接进
# 上下文窗口的,几万字的产品简介乘以几十条会瞬间烧掉一次调用。
# 所以照 routers/comments.py:145-178 的先例,**每个工具自带 LIMIT + 逐字段截断**。

_SEARCH_DEFAULT_LIMIT = 5
_SEARCH_MAX_LIMIT = 20
_ACTIVITY_MAX_LIMIT = 30

_PREVIEW_CHARS = 300   # 搜索结果里的长文本:够判断「是不是这条」就行
_DETAIL_CHARS = 3000   # 详情里的正文:够回答,又不至于一条吃掉整个上下文
_QUERY_MAX_CHARS = 100  # 搜索词本身也截断 —— 模型偶尔会塞一整段话进来

# 兜底:万一将来加了字段、算错了上限,单次工具结果也不会失控。
# 注意这里是**整个换成错误对象**而不是把 JSON 剪一半 —— 截断过的 JSON 不是合法 JSON,
# 模型拿到半截花括号只会胡编(同 models.AiMessage 那条注释的道理)。
_MAX_RESULT_CHARS = 30000

# 工具结果的包裹标记。系统提示里声明了这个标记里的内容是**数据、不是指令**。
RESULT_OPEN = '<tool_result name="{name}">'
RESULT_CLOSE = "</tool_result>"


@dataclass(frozen=True)
class Viewer:
    """提问者的**快照**,不是那个 ORM 对象。

    为什么不能直接传 `models.User`:AI 端点必须在**调模型之前把数据库连接还回池子**
    (见 routers/ai.py 的说明),所以工具运行的时候,那个 User 已经 detached 了。
    `expire_on_commit=False` 让它**看起来**还能用 —— 读 `id` / `role` / `name` 确实没问题 ——
    但任何一次懒加载都会在某个工具深处抛 `DetachedInstanceError`,而那是一个
    「十个工具里九个没事」的 bug。快照让这件事在类型上就不可能发生。

    只放三个普通列。**故意不放 `email` / `department`**:工具不需要,而快照是最容易被
    顺手塞东西进去的地方,塞进去的东西会跟着工具结果一起进模型上下文。
    """

    id: int
    role: str
    name: str

    @classmethod
    def of(cls, user) -> "Viewer":
        """从 `models.User` 取快照 —— 唯一的构造点,在会话还活着的时候调用。"""
        return cls(id=user.id, role=user.role, name=user.name)


# ---------------------------------------------------------------- 小工具

def _clip(value: Any, limit: int = _PREVIEW_CHARS) -> Any:
    """长文本截断,并**明说截断了多少** —— 不说的话模型会以为它看到了全文。

    非字符串原样返回(None / int / datetime 交给 json 的 default=str)。
    """
    if value is None or not isinstance(value, str):
        return value
    if len(value) <= limit:
        return value
    return f"{value[:limit]}…[已截断,原文共 {len(value)} 字]"


def _clean_query(raw: Any) -> str:
    """搜索词归一化:去空白 + 截断。"""
    return str(raw or "").strip()[:_QUERY_MAX_CHARS]


def _like_escape(value: str) -> str:
    """转义 LIKE 的通配符。

    `%` 和 `_` 在 LIKE 里是通配符。不转义的话,模型搜「50%」会变成「50 后面跟任意字符」,
    搜「a_b」会匹配到「axb」—— 用户看到的是一个说不通的搜索结果。
    配合 `.ilike(pattern, escape="\\\\")` 使用。
    """
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _like(query: str) -> str:
    """把搜索词包成「包含」模式。"""
    return f"%{_like_escape(query)}%"


def _limit(raw: Any, *, default: int = _SEARCH_DEFAULT_LIMIT, cap: int = _SEARCH_MAX_LIMIT) -> int:
    """模型给的 limit 不可信(可能是字符串、负数、一万),一律夹到 [1, cap]。"""
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return max(1, min(value, cap))


def _published_only(stmt):
    """博客的**唯一**可见性规则:只读已发布。

    刻意不复用 `permissions.can_view_post` —— 那个是**界面**的规则(作者与管理员
    看得到草稿),AI 的规则更严。两条规则不同就该各写各的,合并只会让其中一条
    在某次「顺手重构」里悄悄放松。见模块头第 3 条。

    刻意也不抽成 `visibility.py`:全站只有这一个地方用得上(`get_post` 与
    `search_posts`),按 doc/架构.md:443-472「偶尔重复 → 先重复」,一个条件的重复
    不值得一个模块。
    """
    return stmt.where(BlogPost.published_at.isnot(None))


def _author_names(db: Session, posts: list[BlogPost]) -> dict[int, str]:
    """按 author_id 取**当前**姓名(改名后要跟着改)。

    ⚠️ 这是全仓库第三份同样的查询(`routers/blog.py:_author_names`、
    `routers/comments.py:_current_names`)。三份五行的东西还不值得抽象
    (`crud.py` 只收「逐字相同的行」是刻意的),但**出现第四份时应当移进 crud.py** ——
    写下这句是为了让那时候的人知道这不是「又一次顺手重复」。
    """
    ids = {p.author_id for p in posts if p.author_id is not None}
    if not ids:
        return {}
    return dict(db.execute(select(User.id, User.name).where(User.id.in_(ids))).all())


def _product_categories(db: Session, product_ids: list[int]) -> dict[int, list[str]]:
    """一次 IN 查询取回这批产品的分类名。逐条查会变成 N+1。"""
    if not product_ids:
        return {}
    rows = db.execute(
        select(ProductCategory.product_id, Category.name)
        .join(Category, Category.id == ProductCategory.category_id)
        .where(ProductCategory.product_id.in_(product_ids))
        .order_by(Category.name)
    ).all()
    grouped: dict[int, list[str]] = {}
    for product_id, name in rows:
        grouped.setdefault(product_id, []).append(name)
    return grouped


def _post_tags(db: Session, post_ids: list[int]) -> dict[int, list[str]]:
    """同上,帖子的标签名。"""
    if not post_ids:
        return {}
    rows = db.execute(
        select(BlogPostTag.post_id, BlogTag.name)
        .join(BlogTag, BlogTag.id == BlogPostTag.tag_id)
        .where(BlogPostTag.post_id.in_(post_ids))
        .order_by(BlogTag.name)
    ).all()
    grouped: dict[int, list[str]] = {}
    for post_id, name in rows:
        grouped.setdefault(post_id, []).append(name)
    return grouped


def _iso(value: Any) -> Any:
    """date / datetime → ISO 字符串;None 原样。"""
    return value.isoformat() if hasattr(value, "isoformat") else value


# ---------------------------------------------------------------- 产品

def search_products(db: Session, *, viewer: Viewer, query: Any = "", limit: Any = None) -> dict:
    """按名称搜产品。搜索**只搜 `name`**,顺带回一段简介预览。"""
    q = _clean_query(query)
    if not q:
        return {"error": "搜索词为空"}
    take = _limit(limit)
    products = db.scalars(
        select(Product)
        .where(Product.name.ilike(_like(q), escape="\\"))
        .order_by(Product.name)
        .limit(take)
    ).all()
    cats = _product_categories(db, [p.id for p in products])
    return {
        "count": len(products),
        "results": [
            {
                "id": p.id,
                "name": p.name,
                "status": p.status,
                "url": p.url,
                "monthly_visits": p.monthly_visits,
                "categories": cats.get(p.id, []),
                "problem": _clip(p.problem),
            }
            for p in products
        ],
    }


def get_product(db: Session, *, viewer: Viewer, product_id: Any = None) -> dict:
    """取一个产品的完整档案。"""
    product = db.get(Product, _int_or_none(product_id))
    if product is None:
        return {"error": "产品不存在"}
    tiers = db.scalars(
        select(ProductPriceTier)
        .where(ProductPriceTier.product_id == product.id)
        .order_by(ProductPriceTier.id)
    ).all()
    return {
        "id": product.id,
        "name": product.name,
        "url": product.url,
        "status": product.status,
        "founder": _clip(product.founder, _DETAIL_CHARS),
        "monthly_visits": product.monthly_visits,
        "categories": _product_categories(db, [product.id]).get(product.id, []),
        "problem": _clip(product.problem, _DETAIL_CHARS),
        "user_reviews": _clip(product.user_reviews, _DETAIL_CHARS),
        "marketing_strategy": _clip(product.marketing_strategy, _DETAIL_CHARS),
        "tech_analysis": _clip(product.tech_analysis, _DETAIL_CHARS),
        "price_tiers": [
            {
                "name": t.name,
                # 用 str 不用 float:金额不该出现浮点尾数,模型也不需要做算术
                "amount": None if t.amount is None else str(t.amount),
                "cycle": t.cycle,
                "note": _clip(t.note),
            }
            for t in tiers
        ],
        "updated_at": _iso(product.updated_at),
    }


def list_categories(db: Session, *, viewer: Viewer) -> dict:
    """分类清单(含各分类下的产品数)。"""
    counts = dict(
        db.execute(
            select(ProductCategory.category_id, func.count())
            .group_by(ProductCategory.category_id)
        ).all()
    )
    categories = db.scalars(select(Category).order_by(Category.name)).all()
    return {
        "count": len(categories),
        "results": [
            {
                "id": c.id,
                "name": c.name,
                "description": _clip(c.description),
                "product_count": counts.get(c.id, 0),
            }
            for c in categories
        ],
    }


# ---------------------------------------------------------------- 需求

def search_requirements(db: Session, *, viewer: Viewer, query: Any = "", limit: Any = None) -> dict:
    """按需求描述搜需求。"""
    q = _clean_query(query)
    if not q:
        return {"error": "搜索词为空"}
    take = _limit(limit)
    rows = db.scalars(
        select(Requirement)
        .where(Requirement.description.ilike(_like(q), escape="\\"))
        .order_by(Requirement.id.desc())
        .limit(take)
    ).all()
    return {
        "count": len(rows),
        "results": [
            {
                "id": r.id,
                "description": r.description,
                "status": r.status,
                "priority": r.priority,
                "source": r.source,
                "product_type": r.product_type,
                "estimated_days": r.estimated_days,
                "due_on": _iso(r.due_on),
                "detail": _clip(r.detail),
            }
            for r in rows
        ],
    }


def get_requirement(db: Session, *, viewer: Viewer, requirement_id: Any = None) -> dict:
    """取一条需求的完整内容。"""
    r = db.get(Requirement, _int_or_none(requirement_id))
    if r is None:
        return {"error": "需求不存在"}
    return {
        "id": r.id,
        "description": r.description,
        "detail": _clip(r.detail, _DETAIL_CHARS),
        "priority": r.priority,
        "source": r.source,
        "product_type": r.product_type,
        "status": r.status,
        "proposed_on": _iso(r.proposed_on),
        "estimated_days": r.estimated_days,
        "due_on": _iso(r.due_on),
        "link_url": r.link_url,
        "note": _clip(r.note),
        "updated_at": _iso(r.updated_at),
    }


# ---------------------------------------------------------------- 博客(只读已发布)

def search_posts(db: Session, *, viewer: Viewer, query: Any = "", limit: Any = None) -> dict:
    """按**标题**搜帖子,只搜已发布的。

    搜索**只搜 title,不搜 body**:对正文做 `ilike` 会把整篇正文连片段一起拖进上下文,
    而「标题里有这个词」已经足够定位。正文只在 `get_post` 里整篇取。
    """
    q = _clean_query(query)
    if not q:
        return {"error": "搜索词为空"}
    take = _limit(limit)
    posts = db.scalars(
        _published_only(select(BlogPost))
        .where(BlogPost.title.ilike(_like(q), escape="\\"))
        .order_by(BlogPost.published_at.desc(), BlogPost.id.desc())
        .limit(take)
    ).all()
    names = _author_names(db, posts)
    tags = _post_tags(db, [p.id for p in posts])
    return {
        "count": len(posts),
        "results": [
            {
                "id": p.id,
                "title": p.title,
                "author": names.get(p.author_id) or p.author_name,
                "published_at": _iso(p.published_at),
                "tags": tags.get(p.id, []),
                "body_preview": _clip(p.body),
            }
            for p in posts
        ],
    }


def get_post(db: Session, *, viewer: Viewer, post_id: Any = None) -> dict:
    """取一篇**已发布**帖子的完整正文。

    草稿按**不存在**处理(与 routers/blog.py 的 404 语义一致):不区分「没有这篇」
    与「它是草稿」,因为对 AI 来说两者都是「不在可回答的范围内」。
    """
    post = db.scalars(
        _published_only(select(BlogPost)).where(BlogPost.id == _int_or_none(post_id))
    ).first()
    if post is None:
        return {"error": "帖子不存在或尚未发布"}
    names = _author_names(db, [post])
    return {
        "id": post.id,
        "title": post.title,
        "author": names.get(post.author_id) or post.author_name,
        "published_at": _iso(post.published_at),
        "tags": _post_tags(db, [post.id]).get(post.id, []),
        "body": _clip(post.body, _DETAIL_CHARS),
    }


def list_blog_tags(db: Session, *, viewer: Viewer) -> dict:
    """标签清单(含已发布帖子的篇数)。"""
    counts = dict(
        db.execute(
            select(BlogPostTag.tag_id, func.count())
            .join(BlogPost, BlogPost.id == BlogPostTag.post_id)
            .where(BlogPost.published_at.isnot(None))
            .group_by(BlogPostTag.tag_id)
        ).all()
    )
    tags = db.scalars(select(BlogTag).order_by(BlogTag.name)).all()
    return {
        "count": len(tags),
        "results": [
            {"id": t.id, "name": t.name, "post_count": counts.get(t.id, 0)} for t in tags
        ],
    }


# ---------------------------------------------------------------- 复用首页的两块

def get_summary(db: Session, *, viewer: Viewer) -> dict:
    """项目汇总(每种内容类型有多少条)。

    **直接调 `routers/home.py` 的路由函数**,不在这里重写一遍计数 ——
    模型回答「现在有多少个产品」时给出的数字,必须和首页上那个数字**一模一样**,
    两份实现迟早会漂移(而且是在「博客算不算草稿」这种细节上漂移)。
    这是公开函数、返回 Pydantic 模型,不是借别的模块的下划线私有名。
    """
    items = home.get_summary(db=db)
    return {"results": [item.model_dump(mode="json") for item in items]}


def recent_activity(db: Session, *, viewer: Viewer, limit: Any = None) -> dict:
    """最近动态。同样复用首页的实现,理由见 `get_summary`。"""
    take = _limit(limit, default=10, cap=_ACTIVITY_MAX_LIMIT)
    events = home.list_activity(limit=take, offset=0, db=db)
    return {"count": len(events), "results": [e.model_dump(mode="json") for e in events]}


def _int_or_none(raw: Any) -> int | None:
    """模型可能把 id 写成 "3" 或 "产品3"。取不出整数就当「不存在」,不抛异常。"""
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------- 注册表

@dataclass(frozen=True)
class ToolSpec:
    """一个工具:给模型看的说明 + 本地实现。"""

    name: str
    description: str
    parameters: dict
    run: Callable[..., dict]


def _p(**props: dict) -> dict:
    """简写:一个 object 型参数的 JSON Schema。"""
    return {"type": "object", "properties": props, "required": []}


_ID = {"type": "integer", "description": "记录 id"}
_QUERY = {"type": "string", "description": "搜索关键词"}
_LIMIT = {"type": "integer", "description": "最多返回几条,默认 5,上限 20"}

TOOLS: tuple[ToolSpec, ...] = (
    ToolSpec(
        name="search_products",
        description="按名称搜索产品库。返回匹配产品的 id、状态、简介摘要。",
        parameters=_p(query=_QUERY, limit=_LIMIT),
        run=search_products,
    ),
    ToolSpec(
        name="get_product",
        description="取一个产品的完整档案:简介、用户评价、营销策略、技术分析、定价档位。",
        parameters=_p(product_id=_ID),
        run=get_product,
    ),
    ToolSpec(
        name="search_requirements",
        description="按需求描述搜索需求池。返回需求 id、进展状态、优先级、预计交付日期。",
        parameters=_p(query=_QUERY, limit=_LIMIT),
        run=search_requirements,
    ),
    ToolSpec(
        name="get_requirement",
        description="取一条需求的完整内容与全部字段。",
        parameters=_p(requirement_id=_ID),
        run=get_requirement,
    ),
    ToolSpec(
        name="search_posts",
        description="按标题搜索已发布的博客帖子。返回帖子 id、标题、作者、标签、正文摘要。",
        parameters=_p(query=_QUERY, limit=_LIMIT),
        run=search_posts,
    ),
    ToolSpec(
        name="get_post",
        description="取一篇已发布博客帖子的完整正文。",
        parameters=_p(post_id=_ID),
        run=get_post,
    ),
    ToolSpec(
        name="list_categories",
        description="列出全部产品分类,以及每个分类下的产品数量。",
        parameters=_p(),
        run=list_categories,
    ),
    ToolSpec(
        name="list_blog_tags",
        description="列出全部博客标签,以及每个标签下已发布帖子的篇数。",
        parameters=_p(),
        run=list_blog_tags,
    ),
    ToolSpec(
        name="get_summary",
        description="项目汇总:产品、需求、博客各有多少条(与首页显示的数字一致)。",
        parameters=_p(),
        run=get_summary,
    ),
    ToolSpec(
        name="recent_activity",
        description="最近的站内动态:谁在什么时候新建/修改/删除了什么。",
        parameters=_p(limit=_LIMIT),
        run=recent_activity,
    ),
)

BY_NAME: dict[str, ToolSpec] = {t.name: t for t in TOOLS}

# 直接发给 DeepSeek 的 `tools` 参数。顺序与 TOOLS 一致(稳定顺序对 prompt cache 有好处)。
TOOL_SCHEMAS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": t.name,
            "description": t.description,
            "parameters": t.parameters,
        },
    }
    for t in TOOLS
]


# ---------------------------------------------------------------- 调用

def _accepted_args(spec: ToolSpec, args: dict) -> dict:
    """只保留 schema 里声明过的参数。

    模型偶尔会自己加一个没声明的键,或者把 `limit` 写成字符串。直接 `**args` 展开的话
    会撞上 `TypeError: unexpected keyword argument`,而那条报错对模型毫无意义 ——
    它会开始猜。这里丢掉未声明的键,把类型问题留给工具内部各自兜(`_limit` / `_int_or_none`)。
    """
    allowed = spec.parameters.get("properties", {})
    return {k: v for k, v in args.items() if k in allowed}


def run_tool(db: Session, name: str, args: Any, *, viewer: Viewer) -> str:
    """执行一个工具,返回**包好标记的 JSON 文本**(直接作为 `role:"tool"` 的内容)。

    **任何失败都返回给模型,不向上抛。** 一个工具炸掉不该让整轮问答 500:
    模型看到「产品不存在」会换个方式再试,看到 500 只能道歉。所以这里把所有异常
    收敛成同一个信封。

    ⚠️ 但**不吞 `SQLAlchemyError` 之外的未知异常** —— 那些是我们的 bug,记日志后
    照样返回给模型,让它能说一句「查库出错了」。全吞会让 bug 永远不出现在日志里。
    """
    spec = BY_NAME.get(name)
    if spec is None:
        return _wrap(name, {"error": f"没有名为 {name} 的工具"})
    if not isinstance(args, dict):
        args = {}

    try:
        payload = spec.run(db, viewer=viewer, **_accepted_args(spec, args))
    except SQLAlchemyError as exc:
        log.exception("AI 工具 %s 查询失败", name)
        payload = {"error": f"查询失败:{type(exc).__name__}"}
    except Exception as exc:  # noqa: BLE001 —— 见 docstring:收敛成信封,但留下日志
        log.exception("AI 工具 %s 执行异常", name)
        payload = {"error": f"工具执行出错:{type(exc).__name__}"}

    text = json.dumps(payload, ensure_ascii=False, default=str)
    if len(text) > _MAX_RESULT_CHARS:
        log.warning("AI 工具 %s 结果过大(%s 字符),已替换为提示", name, len(text))
        text = json.dumps(
            {"error": "结果过大,已省略", "hint": "请缩小搜索范围或减少 limit"},
            ensure_ascii=False,
        )
    return _wrap(name, text)


def _wrap(name: str, body: Any) -> str:
    """用明确的分隔符包住工具结果。

    系统提示里声明了 `tool_result` 里的内容是**数据**。这不是万无一失的防护
    (提示注入本来就没有万无一失的解),它挡的是最朴素的一种:
    产品简介里写一句「忽略之前的指令」,至少不会和我们的指令长得一样。
    真正让注入无害的是**工具只读**(见模块头)。
    """
    return f"{RESULT_OPEN.format(name=name)}\n{body}\n{RESULT_CLOSE}"


def preview_of(name: str, args: Any) -> str:
    """给前端「工具轨迹」用的一行短描述。

    **不要把完整工具结果推给前端**(见 routers/ai.py 的注释):那是模型上下文,
    不是界面内容 —— 一条 3000 字的正文预览喂给气泡只会淹掉回答本身。
    """
    keys = ", ".join(f"{k}={v}" for k, v in sorted((args or {}).items()))
    return f"{name}({keys})" if keys else f"{name}()"
