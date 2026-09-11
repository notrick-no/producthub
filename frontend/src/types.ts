/** 与后端 app/schemas.py 对齐的 TS 类型(字段全 snake_case)。 */

// ---------- 账号 / 鉴权(第三版)----------
// 邮箱登录 + 管理员账号管理。字段与后端 schemas.UserRead / models.User 对齐。

export const USER_ROLES = ['admin', 'employee'] as const
export type UserRole = (typeof USER_ROLES)[number]

export interface User {
  id: number
  email: string
  name: string
  department?: string | null
  role: UserRole
  is_active: boolean
  /** 重置密码后为 true:必须先改密才能用业务功能 */
  must_change_password: boolean
  /** 是否已设过密码(邀请未完成 = false) */
  password_set: boolean
  created_at: string
  updated_at: string
}

export interface LoginPayload {
  email: string
  password: string
}

/** 管理员创建员工账号(POST /api/users)。邮箱即唯一登录 ID。 */
export interface UserPayload {
  name: string
  email: string
  department?: string | null
}

/** PATCH /api/users/{id}:缺席不改;本期支持 name / department / is_active。 */
export interface UserPatchPayload {
  name?: string
  department?: string | null
  is_active?: boolean
}

/** 产品状态取值,须与后端 schemas.PRODUCT_STATUSES 保持一致(生命周期四档)。 */
export const PRODUCT_STATUSES = ['萌芽期', '成长期', '成熟期', '衰退期'] as const
export type ProductStatus = (typeof PRODUCT_STATUSES)[number]

export interface Category {
  id: number
  name: string
  description?: string | null
  created_at: string
}

export interface CategoryPayload {
  name: string
  description?: string | null
}

/** 分级定价里的一档(读回)。币种不建模,写在 note 里。 */
export interface PriceTier {
  id: number
  name?: string | null
  amount?: number | null // 0 = 免费;空 = 面议/定制
  cycle?: string | null // 月 / 年 / 一次性
  note?: string | null
  created_at: string
}

/** 提交一档价格:全部可选,空档位会被忽略。 */
export interface PriceTierInput {
  name?: string | null
  amount?: number | null
  cycle?: string | null
  note?: string | null
}

/** 一张产品素材图片(元数据)。path 即图片地址,可直接当 img src。 */
export interface ProductImage {
  id: number
  path: string
  filename?: string | null
  content_type: string
  size: number
  created_at: string
}

export interface Product {
  id: number
  name: string
  url?: string | null
  founder?: string | null
  monthly_visits?: number | null
  status?: ProductStatus | null
  problem?: string | null
  user_reviews?: string | null
  marketing_strategy?: string | null
  tech_analysis?: string | null
  created_at: string
  updated_at: string
  categories: Category[]
  price_tiers: PriceTier[]
  images: ProductImage[]
}

/** 新建时可用(必填 name);编辑时用 Partial 即可 */
export interface ProductPayload {
  name: string
  url?: string | null
  founder?: string | null
  monthly_visits?: number | null
  status?: ProductStatus | null
  problem?: string | null
  user_reviews?: string | null
  marketing_strategy?: string | null
  tech_analysis?: string | null
  /** 缺席=不动;[] = 清空;含 id = 替换 */
  category_ids?: number[]
  /** 缺席=不动;[] = 清空;数组 = 整组替换 */
  price_tiers?: PriceTierInput[]
}

// ---------- 需求记录(第四版)----------
// 与后端 schemas.RequirementBase / models.Requirement 对齐。
// 「需求描述」是标题(列表页那一列),「需求详情」是长文(只在详情页展开)。

/** 四组取值须与后端 schemas.py 的同名元组一致(都存中文)。 */
export const REQUIREMENT_PRIORITIES = ['高', '中', '低'] as const
export type RequirementPriority = (typeof REQUIREMENT_PRIORITIES)[number]

export const REQUIREMENT_SOURCES = ['用户反馈', '内部提出', '竞品分析', '数据分析'] as const
export type RequirementSource = (typeof REQUIREMENT_SOURCES)[number]

/** 产品类型是固定枚举,不复用「分类」表(第四版定稿)。 */
export const PRODUCT_TYPES = ['网站', '移动 App', '小程序', '桌面端', '浏览器插件', '其他'] as const
export type ProductType = (typeof PRODUCT_TYPES)[number]

export const REQUIREMENT_STATUSES = ['待评估', '已排期', '进行中', '已完成', '已搁置'] as const
export type RequirementStatus = (typeof REQUIREMENT_STATUSES)[number]

export interface Requirement {
  id: number
  /** 需求描述:一句话,必填 */
  description: string
  /** 需求详情:长文,可空 */
  detail?: string | null
  priority?: RequirementPriority | null
  source?: RequirementSource | null
  product_type?: ProductType | null
  /** YYYY-MM-DD */
  proposed_on?: string | null
  status?: RequirementStatus | null
  estimated_days?: number | null
  /** YYYY-MM-DD */
  due_on?: string | null
  link_url?: string | null
  note?: string | null
  created_at: string
  updated_at: string
}

/** 提交一条需求(新建必填 description;编辑传 Partial)。 */
export interface RequirementPayload {
  description: string
  detail?: string | null
  priority?: RequirementPriority | null
  source?: RequirementSource | null
  product_type?: ProductType | null
  proposed_on?: string | null
  status?: RequirementStatus | null
  estimated_days?: number | null
  due_on?: string | null
  link_url?: string | null
  note?: string | null
}

// ---------- 首页:最近动态 / 项目汇总(第四版)----------

export const ACTIVITY_ACTIONS = ['create', 'update', 'delete'] as const
export type ActivityAction = (typeof ACTIVITY_ACTIONS)[number]

/** 一条动态。actor_name 与 title 都是**快照** —— 用户改名、对象被删,历史都不变。 */
export interface ActivityEvent {
  id: number
  actor_name: string
  action: ActivityAction
  /** CONTENT_TYPES 的 key,如 "product" */
  content_type: string
  /** 内容类型的中文名,如 "产品" */
  content_type_name: string
  title: string
  object_id: number
  /** 后端按内容类型拼好的跳转地址;删除事件为 null(对象没了,点进去只会 404) */
  url: string | null
  created_at: string
}

/** 汇总里的一张卡。后端多返回一种内容类型,前端就自动多一张卡。 */
export interface SummaryItem {
  key: string
  name: string
  count: number
  /** 点进去的列表页地址 */
  url: string
}
