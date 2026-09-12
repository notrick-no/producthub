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
from datetime import date, datetime
from typing import Annotated, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, field_validator


def _blank_to_none(v):
    """空串 / 纯空白 → None。

    表单里「没填」和「清空」都长成空串,入库统一成 NULL —— 免得 '' 和 NULL 两种空
    在筛选、去重、展示上各表现一次。

    返回的是原值不是 strip 后的值:各模型都开了 str_strip_whitespace,去空白由它统一做,
    这里只管判空,两件事不掺在一起。
    """
    if isinstance(v, str) and not v.strip():
        return None
    return v


# 「空串 = 没填」的可空文本字段用这个类型,写法:`url: BlankToNone = None`。
# 原来是七个模型各写一份一模一样的 field_validator(PriceTierInput 那份已经是多字段版),
# 现在只有这一处 —— 加字段时挂上类型即可,不会再漏写。
BlankToNone = Annotated[str | None, BeforeValidator(_blank_to_none)]

# 返回给前端的 Schema 需要能直接读 ORM 对象(model_config 被子类继承)

# 产品状态:单选,产品生命周期四档(见 第二版产品-开发中.md;空 = 未设置)
PRODUCT_STATUSES = ("萌芽期", "成长期", "成熟期", "衰退期")
ProductStatus = Literal["萌芽期", "成长期", "成熟期", "衰退期"]


class CategoryBase(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=100)
    description: BlankToNone = None


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

    name: BlankToNone = Field(default=None, max_length=100)  # 档位名,如 Pro / 团队版
    amount: float | None = Field(default=None, ge=0)  # 0 = 免费;空 = 面议/定制
    cycle: BlankToNone = Field(default=None, max_length=20)  # 月 / 年 / 一次性
    note: BlankToNone = None


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
    url: BlankToNone = Field(default=None, max_length=2048)
    founder: str | None = None
    monthly_visits: int | None = Field(default=None, ge=0)
    status: ProductStatus | None = None  # 产品状态
    problem: str | None = None
    user_reviews: str | None = None
    marketing_strategy: str | None = None
    tech_analysis: BlankToNone = None  # 产品技术分析


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
    # 最后一次**成功**登录(第五版,来自 login_events)。只在账号列表里带出来,
    # /auth/me 等处没有这个查询,留 None —— 所以是可选而非必填。
    last_login_at: datetime | None = None
    last_login_ip: str | None = None
    created_at: datetime
    updated_at: datetime


def user_read_from_model(user, last_login=None) -> UserRead:
    """ORM User → UserRead(补派生字段 password_set;可选带最近一次成功登录)。"""
    return UserRead(
        id=user.id,
        email=user.email,
        name=user.name,
        department=user.department,
        role=user.role,
        is_active=user.is_active,
        must_change_password=user.must_change_password,
        password_set=bool(user.password_hash),
        last_login_at=last_login.created_at if last_login else None,
        last_login_ip=last_login.ip if last_login else None,
        created_at=user.created_at,
        updated_at=user.updated_at,
    )


class UserCreate(BaseModel):
    """管理员创建员工账号(POST /api/users)。邮箱即唯一登录 ID。"""

    model_config = ConfigDict(str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=255)
    email: str = Field(max_length=255)
    department: BlankToNone = Field(default=None, max_length=100)

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


class UserUpdate(BaseModel):
    """PATCH /api/users/{id}。缺席 = 不改;本期支持 name / department / is_active。

    邮箱与 role 不可改(邮箱是唯一 ID;role 本期不支持在界面升降级)。
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    name: str | None = Field(default=None, min_length=1, max_length=255)
    department: BlankToNone = Field(default=None, max_length=100)
    is_active: bool | None = None


# ---------- 需求 / 动态(第四版)----------
# 需求四个枚举字段都存中文(与产品状态一个路数),取值须与前端 src/requirementMeta.ts
# 的数组保持一致。字段全可空,方便先把需求记下来、细节以后再补。

REQUIREMENT_PRIORITIES = ("高", "中", "低")
REQUIREMENT_SOURCES = ("用户反馈", "内部提出", "竞品分析", "数据分析")
PRODUCT_TYPES = ("网站", "移动 App", "小程序", "桌面端", "浏览器插件", "其他")
REQUIREMENT_STATUSES = ("待评估", "已排期", "进行中", "已完成", "已搁置")

RequirementPriority = Literal["高", "中", "低"]
RequirementSource = Literal["用户反馈", "内部提出", "竞品分析", "数据分析"]
ProductType = Literal["网站", "移动 App", "小程序", "桌面端", "浏览器插件", "其他"]
RequirementStatus = Literal["待评估", "已排期", "进行中", "已完成", "已搁置"]


class RequirementBase(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    description: str = Field(min_length=1, max_length=255)  # 需求描述(标题)
    detail: str | None = None  # 需求详情(长文)
    priority: RequirementPriority | None = None
    source: RequirementSource | None = None
    product_type: ProductType | None = None
    proposed_on: date | None = None  # 提出日期
    status: RequirementStatus | None = None
    estimated_days: int | None = Field(default=None, ge=0)  # 预估投入天数
    due_on: date | None = None  # 预计交付日期
    link_url: BlankToNone = Field(default=None, max_length=2048)  # 相关资料链接
    note: str | None = None


class RequirementCreate(RequirementBase):
    """POST /api/requirements 请求体。"""


class RequirementUpdate(RequirementBase):
    """PATCH /api/requirements/{id} 请求体。全部可选;缺席即不改。"""

    description: str | None = Field(default=None, min_length=1, max_length=255)


class RequirementRead(RequirementBase):
    model_config = ConfigDict(from_attributes=True, str_strip_whitespace=True)

    id: int
    created_at: datetime
    updated_at: datetime


# 动态的三种动作。写事件时用 crud.ACTION_* 那三个常量,它们就取这里的取值 ——
# 两端共用一个 Literal,不会再出现「常量改了、类型没改」这种对不上的情况。
EVENT_ACTIONS = ("create", "update", "delete")
EventAction = Literal["create", "update", "delete"]


class ActivityEventRead(BaseModel):
    """首页动态流里的一条。

    `url` 由后端按内容类型拼(见 app/content_types.py),前端不用知道路径怎么拼;
    **删除事件为 None** —— 对象已经没了,渲染成链接只会在点击时 404。
    """

    id: int
    actor_name: str
    action: EventAction
    content_type: str
    content_type_name: str  # 中文名,如「产品」
    title: str
    object_id: int
    url: str | None
    created_at: datetime


class SummaryItem(BaseModel):
    """首页「项目汇总」的一张卡:某类内容的现有条数。"""

    key: str
    name: str  # 中文名,如「需求」
    count: int
    url: str


# ---------- 评论 / 点赞(第五版)----------
# 产品、需求、以后的博客共用这一套。target_type 的合法取值不在这里写死 ——
# 它取自 app/content_types.py 的 BY_KEY(「有哪些内容类型」的唯一出处),
# 在这里再抄一份就会有两份清单,加内容类型时漏改一份不会报错。

# 单条评论上限。全站没有富文本,评论就是一坨纯文本 —— 这个数只用来挡住
# 「把整个文件粘进输入框」,不是产品上的限制。
COMMENT_MAX_LEN = 5000


class CommentCreate(BaseModel):
    """POST /api/comments 请求体。"""

    model_config = ConfigDict(str_strip_whitespace=True)

    target_type: str = Field(min_length=1, max_length=20)
    target_id: int
    body: str = Field(min_length=1, max_length=COMMENT_MAX_LEN)
    # 有值 = 回复。只允许一层:它必须指向一条顶层评论,见 routers/comments.py
    parent_id: int | None = None


class CommentRead(BaseModel):
    """一条评论。回复以 `replies` 嵌在顶层评论里返回,前端不用自己拼树。

    `author_name` 是**当前**姓名(后端按 author_id join 出来的);账号被删之后
    才回落到写入时记下的那个名字。`body` 在墓碑(已删除)时是空串 ——
    看 `deleted_at` 决定渲染成「该评论已删除」。
    """

    id: int
    target_type: str
    target_id: int
    author_id: int | None
    author_name: str
    body: str
    parent_id: int | None
    deleted_at: datetime | None
    like_count: int
    liked_by_me: bool
    replies: list["CommentRead"] = Field(default_factory=list)
    created_at: datetime


# 自引用模型要显式重建一次,否则 replies 里那个前向引用在首次用到处才解析
CommentRead.model_rebuild()
