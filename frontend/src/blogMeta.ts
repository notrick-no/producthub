import type { BlogStatus } from './types'

/** 帖子状态在列表 / 详情里的 Tag 颜色(仅展示层用)。
 *
 *  名字里带 blog:产品那边也有一个 PRODUCT_STATUS_COLOR,需求那边有
 *  REQUIREMENT_STATUS_COLOR —— 都叫 STATUS_COLOR 时,光看 import 那一行分不清。
 *
 *  草稿用 default(灰)而不是橙色:它不是「警告」,只是还没露面。
 *  一眼能在列表里把灰标签摘出来,就是它该起的作用。 */
export const BLOG_STATUS_COLOR: Record<BlogStatus, string> = {
  draft: 'default',
  published: 'green',
}
