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
