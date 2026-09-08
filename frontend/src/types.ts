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

/** 一条发展历程节点(读回)。date 为 ISO 'YYYY-MM-DD'(界面按年月采集,日固定 01)。 */
export interface ProductMilestone {
  id: number
  date: string
  title: string
  note?: string | null
  created_at: string
}

/** 提交发展历程节点:date 传 'YYYY-MM'(补到 1 号)或 'YYYY-MM-DD'。 */
export interface MilestoneInput {
  date: string
  title: string
  note?: string | null
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
  milestones: ProductMilestone[]
  price_tiers: PriceTier[]
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
  milestones?: MilestoneInput[]
  /** 缺席=不动;[] = 清空;数组 = 整组替换 */
  price_tiers?: PriceTierInput[]
}
