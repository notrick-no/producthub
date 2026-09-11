import type { ProductStatus } from './types'

/** 产品的「产品状态」颜色(仅展示层用)。名字里带 product:需求那边也有一张状态
 *  颜色表(requirementMeta.ts),两个都叫 STATUS_COLOR 时,光看 import 那一行分不清。 */
export const PRODUCT_STATUS_COLOR: Record<ProductStatus, string> = {
  萌芽期: 'gold',
  成长期: 'green',
  成熟期: 'blue',
  衰退期: 'default',
}
