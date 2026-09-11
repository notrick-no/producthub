import type {
  ProductType,
  RequirementPriority,
  RequirementSource,
  RequirementStatus,
} from './types'

/** 需求四个枚举在列表 / 详情里的 Tag 颜色(仅展示层用)。 */

export const PRIORITY_COLOR: Record<RequirementPriority, string> = {
  高: 'red',
  中: 'orange',
  低: 'default',
}

/** 需求的「进展状态」颜色。名字里带 requirement:产品那边也有一个状态颜色表,
 *  两个都叫 STATUS_COLOR 时,光看 import 那一行分不清是哪一个。 */
export const REQUIREMENT_STATUS_COLOR: Record<RequirementStatus, string> = {
  待评估: 'default',
  已排期: 'blue',
  进行中: 'processing',
  已完成: 'green',
  已搁置: 'default',
}

export const PRODUCT_TYPE_COLOR: Record<ProductType, string> = {
  网站: 'blue',
  '移动 App': 'green',
  小程序: 'cyan',
  桌面端: 'purple',
  浏览器插件: 'orange',
  其他: 'default',
}

export const SOURCE_COLOR: Record<RequirementSource, string> = {
  用户反馈: 'blue',
  内部提出: 'purple',
  竞品分析: 'orange',
  数据分析: 'cyan',
}
