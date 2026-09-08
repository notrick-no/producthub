"""Producthut 数据模型(SQLAlchemy 2.0 声明式)。

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

from sqlalchemy import Date, DateTime, ForeignKey, Integer, Numeric, String, Text, func
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

    # 发展历程节点:随产品删除一并删除
    milestones: Mapped[list["ProductMilestone"]] = relationship(
        back_populates="product", cascade="all, delete-orphan"
    )

    # 分级定价档位:随产品删除一并删除
    price_tiers: Mapped[list["ProductPriceTier"]] = relationship(
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


class ProductMilestone(Base):
    """产品发展历程里的一条关键节点(Product Timeline)。

    存精确 date;界面按「年月」采集(day 固定为 1 号),需要精确到日也支持。
    """

    __tablename__ = "product_milestones"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    product_id: Mapped[int] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"), index=True
    )
    date: Mapped[date] = mapped_column(Date)
    title: Mapped[str] = mapped_column(String(255))  # 事件名,如「上线 MVP」
    note: Mapped[str | None] = mapped_column(Text)  # 补充说明(可选)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    product: Mapped[Product] = relationship(back_populates="milestones")


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
