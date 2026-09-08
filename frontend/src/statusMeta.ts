import type { ProductStatus } from './types'

/** 各状态在列表 / 详情里的 Tag 颜色(仅展示层用)。 */
export const STATUS_COLOR: Record<ProductStatus, string> = {
  调研中: 'gold',
  已上线: 'green',
  快速增长: 'volcano',
  稳定: 'blue',
  衰退: 'default',
  已关闭: 'red',
}
