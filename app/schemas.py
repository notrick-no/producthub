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
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

# 返回给前端的 Schema 需要能直接读 ORM 对象(model_config 被子类继承)

# 产品状态:单选,产品生命周期四档(见 第二版产品-开发中.md;空 = 未设置)
PRODUCT_STATUSES = ("萌芽期", "成长期", "成熟期", "衰退期")
ProductStatus = Literal["萌芽期", "成长期", "成熟期", "衰退期"]


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
    created_at: datetime


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
    price_tiers: list[PriceTierInput] = Field(default_factory=list)


class ProductUpdate(ProductBase):
    """PATCH /api/products/{id} 请求体。全部可选;缺席即不改。

    category_ids 缺席 = 不动分类;[] = 清空;[1,2] = 替换成这组。
    price_tiers 缺席 = 不动;[] = 清空;数组 = 整组替换(同样语义)。
    """

    name: str | None = Field(default=None, min_length=1, max_length=255)
    category_ids: list[int] | None = None
    price_tiers: list[PriceTierInput] | None = None


class ProductRead(ProductBase):
    model_config = ConfigDict(from_attributes=True, str_strip_whitespace=True)

    id: int
    created_at: datetime
    updated_at: datetime
    # 完整对象列表,而非仅 id
    categories: list[CategoryRead] = Field(default_factory=list)
    price_tiers: list[PriceTierRead] = Field(default_factory=list)
    images: list[ProductImageRead] = Field(default_factory=list)


# ---------- 账号 / 鉴权(第三版)----------
# 邮箱登录 + 管理员账号管理。账号字段 snake_case 对齐 models.User;
# 返回给前端的 UserRead 绝不带 password_hash / 各类 token 字段。

USER_ROLES = ("admin", "employee")
UserRole = Literal["admin", "employee"]

PASSWORD_MIN = 8
PASSWORD_MAX = 72  # bcrypt 只哈希前 72 字节,超出会静默截断


def _is_email(v: str) -> bool:
    """轻量邮箱校验(内部工具,不引 email-validator):本地@域名 且域名含点、无空白。"""
    if not v or any(ch.isspace() for ch in v):
        return False
    local, sep, domain = v.partition("@")
    return bool(sep and local and domain and "." in domain)


class LoginRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    email: str
    password: str


class ChangePasswordRequest(BaseModel):
    """登录后改自己密码(old = 当前/临时密码)。"""

    old_password: str
    new_password: str = Field(min_length=PASSWORD_MIN, max_length=PASSWORD_MAX)


class SetPasswordRequest(BaseModel):
    """邀请链接设初始密码(公开,token 一次性)。"""

    token: str
    new_password: str = Field(min_length=PASSWORD_MIN, max_length=PASSWORD_MAX)


class UserRead(BaseModel):
    """返回给前端的账号(不含哈希与 token 字段)。"""

    id: int
    email: str
    name: str
    department: str | None
    role: UserRole
    is_active: bool
    must_change_password: bool
    password_set: bool  # 是否已设过密码(派生:哈希非空)
    created_at: datetime
    updated_at: datetime


def user_read_from_model(user) -> UserRead:
    """ORM User → UserRead(补派生字段 password_set)。"""
    return UserRead(
        id=user.id,
        email=user.email,
        name=user.name,
        department=user.department,
        role=user.role,
        is_active=user.is_active,
        must_change_password=user.must_change_password,
        password_set=bool(user.password_hash),
        created_at=user.created_at,
        updated_at=user.updated_at,
    )


class UserCreate(BaseModel):
    """管理员创建员工账号(POST /api/users)。邮箱即唯一登录 ID。"""

    model_config = ConfigDict(str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=255)
    email: str = Field(max_length=255)
    department: str | None = Field(default=None, max_length=100)

    @field_validator("email", mode="before")
    @classmethod
    def _normalize_email(cls, v):
        if isinstance(v, str):
            v = v.strip().lower()
        return v

    @field_validator("email")
    @classmethod
    def _email_must_be_valid(cls, v):
        if not _is_email(v):
            raise ValueError("请输入有效邮箱")
        return v

    @field_validator("department", mode="before")
    @classmethod
    def _blank_department_to_none(cls, v):
        if isinstance(v, str) and not v.strip():
            return None
        return v


class UserUpdate(BaseModel):
    """PATCH /api/users/{id}。缺席 = 不改;本期支持 name / department / is_active。

    邮箱与 role 不可改(邮箱是唯一 ID;role 本期不支持在界面升降级)。
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    name: str | None = Field(default=None, min_length=1, max_length=255)
    department: str | None = Field(default=None, max_length=100)
    is_active: bool | None = None

    @field_validator("department", mode="before")
    @classmethod
    def _blank_department_to_none(cls, v):
        if isinstance(v, str) and not v.strip():
            return None
        return v
