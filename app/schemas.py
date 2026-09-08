"""API 输入/输出 Schema(Pydantic v2)。

与 产品.md + app/models.py 对齐,字段全 snake_case。
命名约定:
  XxxBase    建/改产品共用的字段集
  XxxCreate  POST 请求体(新增必填项 / 默认值)
  XxxUpdate  PATCH 请求体(全部可选;请求体里没出现的键 = 不改动)
  XxxRead    接口返回(含 id / 时间戳 / 关联的完整对象)

契约决策:
  - url 宽松校验:去首尾空白,空串按 None 处理,不强制带 scheme
  - PATCH 的 category_ids: 缺席=不动;[] = 清空;[1,2] = 替换
  - monthly_visits 存原始整数,禁止负数
"""
import re
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

# 返回给前端的 Schema 需要能直接读 ORM 对象(model_config 被子类继承)

# 产品状态:单选,允许取值见 第二版产品-开发中.md(空 = 未设置/调研早期)
PRODUCT_STATUSES = ("调研中", "已上线", "快速增长", "稳定", "衰退", "已关闭")
ProductStatus = Literal["调研中", "已上线", "快速增长", "稳定", "衰退", "已关闭"]


class CategoryBase(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=100)
    description: str | None = None

    @field_validator("description", mode="before")
    @classmethod
    def _blank_description_to_none(cls, v):
        if isinstance(v, str) and not v.strip():
            return None
        return v


class CategoryCreate(CategoryBase):
    """POST /api/categories 请求体。"""


class CategoryUpdate(CategoryBase):
    """PATCH /api/categories/{id} 请求体。全部可选;缺席即不改。"""

    name: str | None = Field(default=None, min_length=1, max_length=100)


class CategoryRead(CategoryBase):
    model_config = ConfigDict(from_attributes=True, str_strip_whitespace=True)

    id: int
    created_at: datetime


class MilestoneInput(BaseModel):
    """发展历程节点(随产品一起提交)。

    date 传 'YYYY-MM' 或 'YYYY-MM-DD'(界面按年月采集,自动补当月 1 号)。
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    date: date
    title: str = Field(min_length=1, max_length=255)
    note: str | None = None

    @field_validator("date", mode="before")
    @classmethod
    def _expand_year_month(cls, v):
        if isinstance(v, str):
            m = re.fullmatch(r"(\d{4})-(\d{1,2})", v.strip())
            if m:  # 只给年月 → 补到当月 1 号
                return f"{m.group(1)}-{int(m.group(2)):02d}-01"
        return v

    @field_validator("note", mode="before")
    @classmethod
    def _blank_note_to_none(cls, v):
        if isinstance(v, str) and not v.strip():
            return None
        return v


class MilestoneRead(BaseModel):
    """读回单个发展历程节点。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    date: date
    title: str
    note: str | None
    created_at: datetime


class PriceTierInput(BaseModel):
    """分级定价里的一档(随产品提交)。币种不建模,可写进 note。"""

    model_config = ConfigDict(str_strip_whitespace=True)

    name: str | None = Field(default=None, max_length=100)  # 档位名,如 Pro / 团队版
    amount: float | None = Field(default=None, ge=0)  # 0 = 免费;空 = 面议/定制
    cycle: str | None = Field(default=None, max_length=20)  # 月 / 年 / 一次性
    note: str | None = None

    @field_validator("name", "cycle", "note", mode="before")
    @classmethod
    def _blank_to_none(cls, v):
        if isinstance(v, str) and not v.strip():
            return None
        return v


class PriceTierRead(BaseModel):
    """读回单档价格。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str | None
    amount: float | None
    cycle: str | None
    note: str | None
    created_at: datetime


class ProductImageRead(BaseModel):
    """一张产品素材图片(元数据;`path` 即图片地址)。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    path: str  # /uploads/<随机名>.<扩展名>,可直接当 img src
    filename: str | None  # 上传时的原始文件名,展示用
    content_type: str
    size: int  # 字节
    caption: str | None  # 图片说明
    sort_order: int
    created_at: datetime


class ImageUpdate(BaseModel):
    """PATCH 一张图片:改说明 / 排序。缺席字段不动。"""

    model_config = ConfigDict(str_strip_whitespace=True)

    caption: str | None = None
    sort_order: int | None = Field(default=None, ge=0)

    @field_validator("caption", mode="before")
    @classmethod
    def _blank_caption_to_none(cls, v):
        if isinstance(v, str) and not v.strip():
            return None
        return v


class ProductBase(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=255)
    url: str | None = Field(default=None, max_length=2048)
    founder: str | None = None
    monthly_visits: int | None = Field(default=None, ge=0)
    status: ProductStatus | None = None  # 产品状态
    problem: str | None = None
    user_reviews: str | None = None
    marketing_strategy: str | None = None
    tech_analysis: str | None = None  # 产品技术分析

    @field_validator("url", mode="before")
    @classmethod
    def _blank_url_to_none(cls, v):
        if isinstance(v, str) and not v.strip():
            return None
        return v

    @field_validator("tech_analysis", mode="before")
    @classmethod
    def _blank_tech_analysis_to_none(cls, v):
        if isinstance(v, str) and not v.strip():
            return None
        return v


class ProductCreate(ProductBase):
    """POST /api/products 请求体。"""

    # 建产品时可直接打标;分类本身走 /api/categories 单独维护
    category_ids: list[int] = Field(default_factory=list)
    milestones: list[MilestoneInput] = Field(default_factory=list)
    price_tiers: list[PriceTierInput] = Field(default_factory=list)


class ProductUpdate(ProductBase):
    """PATCH /api/products/{id} 请求体。全部可选;缺席即不改。

    category_ids 缺席 = 不动分类;[] = 清空;[1,2] = 替换成这组。
    milestones / price_tiers 缺席 = 不动;[] = 清空;数组 = 整组替换(同样语义)。
    """

    name: str | None = Field(default=None, min_length=1, max_length=255)
    category_ids: list[int] | None = None
    milestones: list[MilestoneInput] | None = None
    price_tiers: list[PriceTierInput] | None = None


class ProductRead(ProductBase):
    model_config = ConfigDict(from_attributes=True, str_strip_whitespace=True)

    id: int
    created_at: datetime
    updated_at: datetime
    # 完整对象列表,而非仅 id
    categories: list[CategoryRead] = Field(default_factory=list)
    milestones: list[MilestoneRead] = Field(default_factory=list)
    price_tiers: list[PriceTierRead] = Field(default_factory=list)
    images: list[ProductImageRead] = Field(default_factory=list)
