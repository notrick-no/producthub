import dayjs from 'dayjs'
import type { Dayjs } from 'dayjs'
import { Button, DatePicker, Input, Space, Typography } from 'antd'
import { DeleteOutlined, PlusOutlined } from '@ant-design/icons'
import type { MilestoneInput } from '../types'

/** 表单里一条「发展历程」草稿(未保存状态)。 */
export interface MilestoneDraft {
  /** 本行本地稳定 key(新增/删除用),非后端 id */
  key: number
  /** 选中的月份(dayjs,展示整月);未选 = null */
  month: Dayjs | null
  title: string
  note: string
}

let _key = 0
const newDraft = (): MilestoneDraft => ({
  key: ++_key,
  month: null,
  title: '',
  note: '',
})

/** 把后端读回的一条节点转成可编辑草稿(date 按整月展开)。 */
export function milestoneToDraft(m: {
  date: string
  title: string
  note?: string | null
}): MilestoneDraft {
  return { key: ++_key, month: dayjs(m.date), title: m.title, note: m.note ?? '' }
}

/**
 * 校验并收集草稿为提交结构。
 * 全空行会被忽略;填了一半的行返回 { error: true },调用方应中止并提示。
 */
export function collectMilestones(
  drafts: MilestoneDraft[],
): { milestones?: MilestoneInput[]; error?: boolean } {
  const filled = drafts.filter((d) => d.month || d.title.trim() || d.note.trim())
  for (const d of filled) {
    if (!d.month || !d.title.trim()) return { error: true }
  }
  return {
    milestones: filled.map((d) => ({
      date: d.month!.format('YYYY-MM'),
      title: d.title.trim(),
      note: d.note.trim() || null,
    })),
  }
}

interface Props {
  drafts: MilestoneDraft[]
  onChange: (drafts: MilestoneDraft[]) => void
}

/** 发展历程编辑器:年月 + 事件名 + 说明,可增删。 */
export default function MilestoneEditor({ drafts, onChange }: Props) {
  const patch = (key: number, part: Partial<MilestoneDraft>) =>
    onChange(drafts.map((d) => (d.key === key ? { ...d, ...part } : d)))
  const remove = (key: number) => onChange(drafts.filter((d) => d.key !== key))

  return (
    <Space direction="vertical" size={8} style={{ width: '100%' }}>
      {drafts.length === 0 ? (
        <Typography.Text type="secondary">还没有节点,点下方按钮添加</Typography.Text>
      ) : (
        drafts.map((d) => (
          <div
            key={d.key}
            style={{ border: '1px solid #f0f0f0', borderRadius: 8, padding: '8px 12px' }}
          >
            <Space size={8} style={{ display: 'flex' }}>
              <DatePicker
                picker="month"
                allowClear
                placeholder="选择时间(年月)"
                style={{ width: 160 }}
                value={d.month}
                onChange={(v) => patch(d.key, { month: v })}
              />
              <Input
                placeholder="事件名称,如 上线 MVP / 开始收费"
                maxLength={255}
                value={d.title}
                onChange={(e) => patch(d.key, { title: e.target.value })}
              />
              <Button type="text" danger icon={<DeleteOutlined />} onClick={() => remove(d.key)} />
            </Space>
            <Input
              allowClear
              placeholder="补充说明(可选)"
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
        添加节点
      </Button>
    </Space>
  )
}
