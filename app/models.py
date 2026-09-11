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

    sessions: Mapped[list["Session"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class Session(Base):
    """一次登录会话:随机 token 的 sha256 存这里,明文只放 HttpOnly cookie。

    显式记录以便「禁用员工 / 重置密码」时一键踢掉该用户全部会话;
    过期或删除后需重新登录。token 本身用 secrets 生成,库里只存哈希。
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
