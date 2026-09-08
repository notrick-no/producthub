import { Button, Input, InputNumber, Select, Space, Typography } from 'antd'
import { DeleteOutlined, PlusOutlined } from '@ant-design/icons'
import type { PriceTierInput } from '../types'

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

let _key = 0
const newDraft = (): PriceTierDraft => ({
  key: ++_key,
  name: '',
  amount: null,
  cycle: null,
  note: '',
})

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

interface Props {
  drafts: PriceTierDraft[]
  onChange: (drafts: PriceTierDraft[]) => void
}

/** 分级定价编辑器:档名 + 金额 + 周期 + 备注,可增删。 */
export default function PriceTierEditor({ drafts, onChange }: Props) {
  const patch = (key: number, part: Partial<PriceTierDraft>) =>
    onChange(drafts.map((d) => (d.key === key ? { ...d, ...part } : d)))
  const remove = (key: number) => onChange(drafts.filter((d) => d.key !== key))

  return (
    <Space direction="vertical" size={8} style={{ width: '100%' }}>
      {drafts.length === 0 ? (
        <Typography.Text type="secondary">
          还没有定价档位。留空表示不记录价格(调研中先不管)。
        </Typography.Text>
      ) : (
        drafts.map((d) => (
          <div
            key={d.key}
            style={{ border: '1px solid #f0f0f0', borderRadius: 8, padding: '8px 12px' }}
          >
            <Space
              size={8}
              style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center' }}
            >
              <Input
                placeholder="档位名,如 Free / Pro / 团队版"
                maxLength={100}
                style={{ flexGrow: 1, minWidth: 160, width: 'auto' }}
                value={d.name}
                onChange={(e) => patch(d.key, { name: e.target.value })}
              />
              <InputNumber
                min={0}
                precision={2}
                placeholder="金额"
                style={{ width: 130 }}
                value={d.amount}
                onChange={(v) => patch(d.key, { amount: v })}
              />
              <Select
                allowClear
                placeholder="周期"
                style={{ width: 110 }}
                value={d.cycle}
                options={PRICE_CYCLES.map((c) => ({ value: c, label: c }))}
                onChange={(v) => patch(d.key, { cycle: v })}
              />
              <Button type="text" danger icon={<DeleteOutlined />} onClick={() => remove(d.key)} />
            </Space>
            <Input
              allowClear
              placeholder="备注(可选),如「含高级功能」「按年付 8 折」——币种也可写在这"
              style={{ marginTop: 6 }}
              value={d.note}
              onChange={(e) => patch(d.key, { note: e.target.value })}
            />
          </div>
        ))
      )}
      <Button
        block
        type="dashed"
        icon={<PlusOutlined />}
        onClick={() => onChange([...drafts, newDraft()])}
      >
        添加档位
      </Button>
    </Space>
  )
}
