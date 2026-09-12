"""producthub 数据模型(SQLAlchemy 2.0 声明式)。

对应 产品.md 的数据库设计三张表:
  products            产品记录
  categories          自定义产品分类
  product_categories  产品-分类 多对多关联表

约定:
  - 表名/列名沿用 产品.md 的下划线命名
  - 除 name 外内容字段都允许为空(调研早期信息可能不全)
  - url 唯一,避免同一网站重复录入
"""
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """所有 ORM 模型的基类。"""


class Product(Base):
    """一条产品调研记录:一个被调研的产品/网站。"""

    __tablename__ = "products"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255))
    url: Mapped[str | None] = mapped_column(String(2048), unique=True)
    founder: Mapped[str | None] = mapped_column(Text)  # 创始人信息(自由文本)
    monthly_visits: Mapped[int | None] = mapped_column(Integer)  # 网站月活量
    status: Mapped[str | None] = mapped_column(String(20))  # 产品状态(见 schemas.PRODUCT_STATUSES)
    problem: Mapped[str | None] = mapped_column(Text)  # 产品解决的问题
    user_reviews: Mapped[str | None] = mapped_column(Text)  # 网站的用户评价
    marketing_strategy: Mapped[str | None] = mapped_column(Text)  # 网站的营销策略
    tech_analysis: Mapped[str | None] = mapped_column(Text)  # 产品技术分析
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # 通过关联表访问分类(用 list["ProductCategory"],详见该类定义)
    category_links: Mapped[list["ProductCategory"]] = relationship(
        back_populates="product", cascade="all, delete-orphan"
    )

    # 分级定价档位:随产品删除一并删除
    price_tiers: Mapped[list["ProductPriceTier"]] = relationship(
        back_populates="product", cascade="all, delete-orphan"
    )

    # 产品素材图片:随产品删除一并删除(文件本体见 app/storage.py 的 uploads/)
    images: Mapped[list["ProductImage"]] = relationship(
        back_populates="product", cascade="all, delete-orphan"
    )


class Category(Base):
    """自定义产品分类。"""

    __tablename__ = "categories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    description: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    product_links: Mapped[list["ProductCategory"]] = relationship(
        back_populates="category", cascade="all, delete-orphan"
    )


class ProductCategory(Base):
    """产品-分类 关联表。

    复合主键 (product_id, category_id) 保证同一产品不会重复打同一个分类。
    删除产品或分类时,关联记录随外键 ON DELETE CASCADE 一并删除。
    """

    __tablename__ = "product_categories"

    product_id: Mapped[int] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"), primary_key=True
    )
    category_id: Mapped[int] = mapped_column(
        ForeignKey("categories.id", ondelete="CASCADE"), primary_key=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    product: Mapped[Product] = relationship(back_populates="category_links")
    category: Mapped[Category] = relationship(back_populates="product_links")


class ProductPriceTier(Base):
    """分级定价里的一档(Free / Pro / 团队版…)。

    amount 数字(0 = 免费),可空(面议/定制);cycle 如 月/年/一次性。
    币种不单独建模,需要时写在 note 里。
    """

    __tablename__ = "product_price_tiers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    product_id: Mapped[int] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str | None] = mapped_column(String(100))  # 档位名,如 Pro
    amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))  # 单价
    cycle: Mapped[str | None] = mapped_column(String(20))  # 月 / 年 / 一次性
    note: Mapped[str | None] = mapped_column(Text)  # 备注(可选)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    product: Mapped[Product] = relationship(back_populates="price_tiers")


class ProductImage(Base):
    """产品素材里的一张图片(**只存元数据**,文件本体落盘 uploads/)。

    `path` 存服务路径(如 `/uploads/ab12cd.png`),浏览器直接当图片地址用;
    `filename` 是上传时的原始文件名,仅作展示,不参与落盘命名(落盘名随机生成)。
    图片无排序、无说明;展示顺序即录入顺序(按 id 升序)。
    """

    __tablename__ = "product_images"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    product_id: Mapped[int] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"), index=True
    )
    path: Mapped[str] = mapped_column(String(2048))  # /uploads/<随机名>.<扩展名>
    filename: Mapped[str | None] = mapped_column(String(255))  # 原始文件名(展示用)
    content_type: Mapped[str] = mapped_column(String(100))
    size: Mapped[int] = mapped_column(Integer)  # 字节
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    product: Mapped[Product] = relationship(back_populates="images")


class User(Base):
    """组织账号(管理员 / 员工)。

    登录走邮箱 + 密码;员工账号由管理员创建、收到邀请邮件后自设密码。
    `password_hash` 为空 = 尚未设过密码(邀请待完成);
    `must_change_password` = 下次登录必须先改密(重置密码后强制);
    `is_active` False = 离职冻结,登录与既有会话都被拒绝。
    邮箱一律存小写(唯一标识);role 取值见 schemas.USER_ROLES。
    """

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String(255), unique=True)  # 登录邮箱(小写)
    name: Mapped[str] = mapped_column(String(255))  # 员工姓名
    department: Mapped[str | None] = mapped_column(String(100))  # 初始部门(可空)
    role: Mapped[str] = mapped_column(
        String(20), server_default=text("'employee'")
    )  # admin / employee
    password_hash: Mapped[str | None] = mapped_column(String(255))  # bcrypt 哈希
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
    must_change_password: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    invite_token_hash: Mapped[str | None] = mapped_column(
        String(64), unique=True
    )  # sha256(邀请 token),一次性,设完密码即清
    invite_token_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    sessions: Mapped[list["AuthSession"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class AuthSession(Base):
    """一次登录会话:随机 token 的 sha256 存这里,明文只放 HttpOnly cookie。

    显式记录以便「禁用员工 / 重置密码」时一键踢掉该用户全部会话;
    过期或删除后需重新登录。token 本身用 secrets 生成,库里只存哈希。

    类名不叫 Session:那个名字被 SQLAlchemy 的会话占着,叫 Session 的话每个
    import 点都得写成 `Session as AuthSession`,读的人还以为存在两个东西。
    表名仍是 sessions,不动库、不需要迁移。
    """

    __tablename__ = "sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)  # sha256(token)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    user: Mapped[User] = relationship(back_populates="sessions")


class LoginEvent(Base):
    """一次登录尝试的审计记录(第五版)。

    **成功与失败都记** —— 审计要回答的往往正是「谁在试」,只留成功记录答不了。

    `email` 存的是**快照**:失败时可能压根没有这个账号(FK 只能置空),而账号被删之后
    也得看得出当时是谁在登。

    `user_id` 是 SET NULL 而非 CASCADE:账号没了,审计记录不能跟着消失。
    这个取舍与 activity_events.actor_id 一致。表只追加,唯一的删除是保留期清理
    (见 app/login_audit.py)。

    不声明与 User 的 relationship(照 activity_events 的先例):这里不需要从账号反查
    它的登录记录,少一个关系就少一处能配错级联的地方。
    """

    __tablename__ = "login_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    email: Mapped[str] = mapped_column(String(255))  # 本次填的邮箱(小写)
    ip: Mapped[str | None] = mapped_column(String(45))  # 45 = IPv6 字面量上限
    user_agent: Mapped[str | None] = mapped_column(String(255))  # 超长在写入前截断
    succeeded: Mapped[bool] = mapped_column(Boolean, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )


class Comment(Base):
    """一条评论(第五版):产品 / 需求 / 以后的博客共用这一张表。

    `target_type` + `target_id` 是**多态外键** —— 没有 FK 约束,所以数据库不知道
    「这条评论挂在哪个产品上」,也就**不会**在删产品时自动带走它的评论。清理由各资源的
    删除端点显式调 `crud.delete_comments_for(...)` 负责。这是这套设计的主要代价。

    层级**只有一层**:顶层评论 `parent_id` 为空,回复指向一条顶层评论;回复不能再被回复。
    校验在 routers/comments.py,不靠数据库约束。

    `deleted_at` 非空 = **墓碑**:`body` 已清空,但这一行还在,所以它下面的回复不会消失。
    硬删一条顶层评论会连带删掉**别人写的**回复 —— 一个人能抹掉一整段自己没参与的讨论。

    `author_name` 只在 `author_id` 为空时兜底署名。与 activity_events 的 actor_name 不同:
    那边存快照是因为它是**某时刻的历史记录**(「张三 3 月删了《X》」就该一直写张三);
    评论是**活的内容**,用户改名后应当跟着改 —— 两处都存等于给同一个事实留两个出处。
    所以展示时按 author_id join 出当前姓名,取不到了才用这一列。
    """

    __tablename__ = "comments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    target_type: Mapped[str] = mapped_column(String(20))  # content_types.BY_KEY 的 key
    target_id: Mapped[int] = mapped_column(Integer)  # 多态:没有 FK,见类注释
    author_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    author_name: Mapped[str] = mapped_column(String(255))  # 仅 author_id 为空时兜底
    body: Mapped[str] = mapped_column(Text, nullable=False)  # 墓碑时清成 ""
    parent_id: Mapped[int | None] = mapped_column(
        ForeignKey("comments.id", ondelete="CASCADE"), index=True
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # 读一个对象的整串评论:定 target 之后按时间正序,一条索引走完
    __table_args__ = (
        Index("ix_comments_target", "target_type", "target_id", "created_at"),
    )

    # 不声明 replies / likes 关系,这是**有意的**:两个子表的外键都是 ON DELETE CASCADE,
    # 而 SQLAlchemy 只在自己管着关系时才去替数据库操心 —— 一旦声明了关系却没加
    # passive_deletes=True,删父行的前一步会先发 UPDATE 把子行「摘」下来:
    # 回复会被改成顶层评论浮上来,而 comment_likes.comment_id 是 NOT NULL,直接
    # IntegrityError 500。这一版不需要从评论反查它的回复或点赞,少一个关系就少一处能写错。


class CommentLike(Base):
    """评论点赞(第五版)。

    照 product_categories 的**关联对象**写法:复合主键 (comment_id, user_id),
    天然保证「一人对一条只能赞一次」,不用额外的唯一约束。
    删评论 / 删账号都靠外键 CASCADE 带走。
    """

    __tablename__ = "comment_likes"

    comment_id: Mapped[int] = mapped_column(
        ForeignKey("comments.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class BlogPost(Base):
    """一篇博客帖子(第五版)。

    **没有 `status` 列**:「已发布 / 草稿」由 `published_at` 有没有值决定 ——
    一个事实一个出处。多存一列 status 就多出一种「两列说法不一致」的状态,而
    `ContentType.published_field` 那套可见性判断也已经认准了 `published_at` 这一列。
    接口上仍旧给前端一个派生的 `status` 字段(见 schemas.BlogPostRead)。

    `published_at` **只在首次发布时写一次**,之后编辑不改:发布时间不是更新时间,
    作者回来改个错别字,不该把帖子顶到最新。

    `author_id` 与评论同款:SET NULL + `author_name` 兜底署名。帖子也是**活的内容**,
    显示时以当前姓名为准(见 routers/blog.py 的 _current_author_name)。
    """

    __tablename__ = "blog_posts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(255))
    body: Mapped[str | None] = mapped_column(Text)
    # 非空 = 已发布;空 = 草稿。这是「发布状态」的唯一出处。
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    author_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    author_name: Mapped[str] = mapped_column(String(255))  # 仅 author_id 为空时兜底
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # 通过关联表访问标签(用 list["BlogPostTag"],写法同 Product.category_links)
    tag_links: Mapped[list["BlogPostTag"]] = relationship(
        back_populates="post", cascade="all, delete-orphan"
    )


class BlogTag(Base):
    """博客标签(第五版)。

    照 categories 那套:独立一张表,**能改名、能按标签筛**。帖子存一串逗号分隔的
    标签就省掉这两张表和那个管理弹窗,但也筛不了、改不了名 —— 标签一旦不能改名,
    写错一个字就只能删了重打。
    """

    __tablename__ = "blog_tags"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    post_links: Mapped[list["BlogPostTag"]] = relationship(
        back_populates="tag", cascade="all, delete-orphan"
    )


class BlogPostTag(Base):
    """帖子-标签 关联表。

    复合主键 (post_id, tag_id) 保证同一帖子不会重复打同一个标签。
    删除帖子或标签时,关联记录随外键 ON DELETE CASCADE 一并删除。
    """

    __tablename__ = "blog_post_tags"

    post_id: Mapped[int] = mapped_column(
        ForeignKey("blog_posts.id", ondelete="CASCADE"), primary_key=True
    )
    tag_id: Mapped[int] = mapped_column(
        ForeignKey("blog_tags.id", ondelete="CASCADE"), primary_key=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    post: Mapped[BlogPost] = relationship(back_populates="tag_links")
    tag: Mapped[BlogTag] = relationship(back_populates="post_links")


class PostLike(Base):
    """帖子点赞(第五版)。

    照 comment_likes 同款:复合主键 (post_id, user_id),天然保证一人一篇只能赞一次。
    删帖子 / 删账号都靠外键 CASCADE 带走。
    """

    __tablename__ = "post_likes"

    post_id: Mapped[int] = mapped_column(
        ForeignKey("blog_posts.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Requirement(Base):
    """一条需求记录(第四版)。

    「需求描述」是标题(一句话,列表页显示的那一列),「需求详情」是长文(详情页展开)。
    四个枚举字段(优先级 / 需求来源 / 产品类型 / 进展状态)都存中文,
    取值见 schemas.REQUIREMENT_*;字段全可空,方便先记下来再补。
    """

    __tablename__ = "requirements"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    description: Mapped[str] = mapped_column(String(255))  # 需求描述(标题)
    detail: Mapped[str | None] = mapped_column(Text)  # 需求详情
    priority: Mapped[str | None] = mapped_column(String(20))  # 优先级:高/中/低
    source: Mapped[str | None] = mapped_column(String(50))  # 需求来源
    product_type: Mapped[str | None] = mapped_column(String(20))  # 产品类型
    proposed_on: Mapped[date | None] = mapped_column(Date)  # 提出日期
    status: Mapped[str | None] = mapped_column(String(20))  # 进展状态
    estimated_days: Mapped[int | None] = mapped_column(Integer)  # 预估投入天数
    due_on: Mapped[date | None] = mapped_column(Date)  # 预计交付日期
    link_url: Mapped[str | None] = mapped_column(String(2048))  # 相关资料链接
    note: Mapped[str | None] = mapped_column(Text)  # 备注
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ActivityEvent(Base):
    """首页「最近动态」里的一条(第四版):谁、何时、对哪个内容、做了什么。

    `actor_name` 与 `title` 都是**快照**:用户会改名、对象会被删,
    动态流不能因为源数据变了就变成空白或 404 —— 所以记下当时的名字和标题。

    `object_id` 在对象被删之后依然留着(删除事件本身也是动态),前端看 `action`
    决定要不要渲染成链接(delete 的对象已经不存在了,点了只会 404)。
    内容类型取值见 app/content_types.py。
    """

    __tablename__ = "activity_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    actor_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    actor_name: Mapped[str] = mapped_column(String(255))  # 操作人姓名快照
    action: Mapped[str] = mapped_column(String(20))  # create / update / delete
    content_type: Mapped[str] = mapped_column(String(50))  # CONTENT_TYPES 的 key
    object_id: Mapped[int] = mapped_column(Integer)  # 对象 id(删后仍保留)
    title: Mapped[str] = mapped_column(String(255))  # 对象标题快照
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
