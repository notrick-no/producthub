import type { ProductStatus } from './types'

/** 各状态在列表 / 详情里的 Tag 颜色(仅展示层用)。 */
export const STATUS_COLOR: Record<ProductStatus, string> = {
  萌芽期: 'gold',
  成长期: 'green',
  成熟期: 'blue',
  衰退期: 'default',
}
