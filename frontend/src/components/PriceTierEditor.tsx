import { Button, Input, InputNumber, Select, Space, Typography } from 'antd'
import { DeleteOutlined, PlusOutlined } from '@ant-design/icons'
import { newDraft, PRICE_CYCLES } from '../priceTierModel'
import type { PriceTierDraft } from '../priceTierModel'

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
