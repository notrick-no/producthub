/** 与后端 app/schemas.py 对齐的 TS 类型(字段全 snake_case)。 */

/** 产品状态取值,须与后端 schemas.PRODUCT_STATUSES 保持一致。 */
export const PRODUCT_STATUSES = ['调研中', '已上线', '快速增长', '稳定', '衰退', '已关闭'] as const
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
}
