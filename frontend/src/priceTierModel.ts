/**
 * 分级定价的「表单模型」:草稿的类型、新建、转换、收集,全在这儿,不碰任何 UI。
 *
 * 本来这些和 PriceTierEditor 挤在一个文件里。拆开有两个原因:一是那个文件同时导出
 * 组件和函数/常量,Fast Refresh 会因此失效(改一次得整页刷新);二是「一档价格怎么
 * 从后端形状变成可编辑形状、再变回提交形状」本来就跟「长什么样」是两件事。
 */
import type { PriceTierInput } from './types'

/** 计费周期可选项(与 第二版产品-开发中.md 一致)。 */
export const PRICE_CYCLES = ['月', '年', '一次性'] as const

/** 表单里一条「定价档位」草稿(未保存状态)。 */
export interface PriceTierDraft {
  /** 本行本地稳定 key(新增/删除用),非后端 id */
  key: number
  name: string
  /** 金额;0 = 免费;null = 面议/定制 */
  amount: number | null
  /** 周期:月 / 年 / 一次性;null = 未选 */
  cycle: string | null
  note: string
}

// 草稿 key 的自增源;只在本模块里发号,组件不关心它怎么来的
let _key = 0

/** 空白的一档。 */
export function newDraft(): PriceTierDraft {
  return { key: ++_key, name: '', amount: null, cycle: null, note: '' }
}

/** 把后端读回的一档价格转成可编辑草稿。 */
export function priceTierToDraft(t: {
  name?: string | null
  amount?: number | null
  cycle?: string | null
  note?: string | null
}): PriceTierDraft {
  return {
    key: ++_key,
    name: t.name ?? '',
    amount: t.amount ?? null,
    cycle: t.cycle ?? null,
    note: t.note ?? '',
  }
}

/**
 * 校验并收集草稿为提交结构。
 * 全空行会被忽略;其余行直接透传(档名可选,半截行后端也接受)。
 */
export function collectPriceTiers(drafts: PriceTierDraft[]): {
  price_tiers?: PriceTierInput[]
} {
  const filled = drafts.filter(
    (d) => d.name.trim() || d.amount != null || d.cycle || d.note.trim(),
  )
  return {
    price_tiers: filled.map((d) => ({
      name: d.name.trim() || null,
      amount: d.amount,
      cycle: d.cycle,
      note: d.note.trim() || null,
    })),
  }
}
