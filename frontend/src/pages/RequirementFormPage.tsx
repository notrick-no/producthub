import { useEffect, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import {
  App as AntApp,
  Button,
  Card,
  DatePicker,
  Divider,
  Form,
  Input,
  InputNumber,
  Select,
  Space,
  Spin,
  Typography,
} from 'antd'
import dayjs from 'dayjs'
import type { Dayjs } from 'dayjs'
import { createRequirement, getRequirement, updateRequirement } from '../api/resources'
import type {
  ProductType,
  RequirementPayload,
  RequirementPriority,
  RequirementSource,
  RequirementStatus,
} from '../types'
import {
  PRODUCT_TYPES,
  REQUIREMENT_PRIORITIES,
  REQUIREMENT_SOURCES,
  REQUIREMENT_STATUSES,
} from '../types'

const { TextArea } = Input

interface FormValues {
  description: string
  detail?: string
  product_type?: ProductType | null
  priority?: RequirementPriority | null
  status?: RequirementStatus | null
  source?: RequirementSource | null
  proposed_on?: Dayjs | null
  due_on?: Dayjs | null
  estimated_days?: number | null
  link_url?: string
  note?: string
}

const DATE_FORMAT = 'YYYY-MM-DD'

/** 把表单值规整成后端可接受的 payload(空串 → null;日期取 YYYY-MM-DD)。 */
function toPayload(values: FormValues): RequirementPayload {
  const text = (v?: string) => (v == null ? null : v.trim() || null)
  return {
    description: values.description.trim(),
    detail: text(values.detail),
    product_type: values.product_type ?? null,
    priority: values.priority ?? null,
    status: values.status ?? null,
    source: values.source ?? null,
    proposed_on: values.proposed_on ? values.proposed_on.format(DATE_FORMAT) : null,
    due_on: values.due_on ? values.due_on.format(DATE_FORMAT) : null,
    estimated_days: values.estimated_days ?? null,
    link_url: text(values.link_url),
    note: text(values.note),
  }
}

/** 新建(/requirements/new)与编辑(/requirements/:id/edit)共用的需求表单页。 */
export default function RequirementFormPage() {
  const { id } = useParams()
  const isEdit = id !== undefined
  const navigate = useNavigate()
  const { message } = AntApp.useApp()

  const [form] = Form.useForm<FormValues>()
  const [loading, setLoading] = useState(isEdit)
  const [saving, setSaving] = useState(false)

  // 编辑模式:预填现有数据(后端给的是 YYYY-MM-DD 字符串,回填成 dayjs)
  useEffect(() => {
    if (!isEdit) return
    let cancelled = false
    setLoading(true)
    getRequirement(Number(id))
      .then((r) => {
        if (cancelled) return
        form.setFieldsValue({
          description: r.description,
          detail: r.detail ?? '',
          product_type: r.product_type ?? null,
          priority: r.priority ?? null,
          status: r.status ?? null,
          source: r.source ?? null,
          proposed_on: r.proposed_on ? dayjs(r.proposed_on) : null,
          due_on: r.due_on ? dayjs(r.due_on) : null,
          estimated_days: r.estimated_days,
          link_url: r.link_url ?? '',
          note: r.note ?? '',
        })
      })
      .catch((err) => {
        if (cancelled) return
        message.error(err instanceof Error ? err.message : '加载需求失败')
        navigate('/requirements', { replace: true })
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [isEdit, id])

  const handleSubmit = async () => {
    let values: FormValues
    try {
      values = await form.validateFields()
    } catch {
      return // 校验失败,antd 已标红
    }

    setSaving(true)
    try {
      const payload = toPayload(values)
      const saved = isEdit
        ? await updateRequirement(Number(id), payload)
        : await createRequirement(payload)
      message.success(isEdit ? '已保存修改' : `已创建「${saved.description}」`)
      navigate(`/requirements/${saved.id}`)
    } catch (err) {
      message.error(err instanceof Error ? err.message : '保存失败')
    } finally {
      setSaving(false)
    }
  }

  if (loading) {
    return (
      <div style={{ textAlign: 'center', padding: 80 }}>
        <Spin tip="加载中…" />
      </div>
    )
  }

  return (
    <Card title={isEdit ? '编辑需求' : '新建需求'} style={{ maxWidth: 760 }}>
      <Form<FormValues>
        form={form}
        layout="vertical"
        onFinish={handleSubmit}
        initialValues={{ estimated_days: null }}
      >
        <Form.Item
          name="description"
          label="需求描述"
          tooltip="一句话说清这条需求,列表页显示的就是它"
          rules={[{ required: true, whitespace: true, message: '请填写需求描述' }]}
        >
          <Input placeholder="如:支持把研究记录导出成 CSV" maxLength={255} />
        </Form.Item>

        <Form.Item name="detail" label="需求详情" tooltip="展开的背景、验收标准、讨论结论等">
          <TextArea rows={6} placeholder="详细描述这条需求(只在详情页展示)" />
        </Form.Item>

        <Divider />

        <Typography.Title level={5}>分类与状态</Typography.Title>

        <Form.Item name="product_type" label="产品类型">
          <Select
            allowClear
            style={{ width: 240 }}
            placeholder="选择产品类型"
            options={PRODUCT_TYPES.map((v) => ({ value: v, label: v }))}
          />
        </Form.Item>

        <Form.Item name="priority" label="优先级">
          <Select
            allowClear
            style={{ width: 240 }}
            placeholder="选择优先级"
            options={REQUIREMENT_PRIORITIES.map((v) => ({ value: v, label: v }))}
          />
        </Form.Item>

        <Form.Item name="status" label="进展状态">
          <Select
            allowClear
            style={{ width: 240 }}
            placeholder="选择进展状态"
            options={REQUIREMENT_STATUSES.map((v) => ({ value: v, label: v }))}
          />
        </Form.Item>

        <Form.Item name="source" label="需求来源">
          <Select
            allowClear
            style={{ width: 240 }}
            placeholder="选择需求来源"
            options={REQUIREMENT_SOURCES.map((v) => ({ value: v, label: v }))}
          />
        </Form.Item>

        <Divider />

        <Typography.Title level={5}>排期与投入</Typography.Title>

        <Form.Item name="proposed_on" label="提出日期">
          <DatePicker style={{ width: 240 }} format={DATE_FORMAT} placeholder="选择日期" />
        </Form.Item>

        <Form.Item name="due_on" label="预计交付日期">
          <DatePicker style={{ width: 240 }} format={DATE_FORMAT} placeholder="选择日期" />
        </Form.Item>

        <Form.Item name="estimated_days" label="预估投入天数">
          <InputNumber
            style={{ width: 240 }}
            min={0}
            precision={0}
            placeholder="预估需要多少天"
            addonAfter="天"
          />
        </Form.Item>

        <Divider />

        <Typography.Title level={5}>其他</Typography.Title>

        <Form.Item name="link_url" label="相关资料链接">
          <Input placeholder="https:// 或直接域名(可不带协议)" maxLength={2048} />
        </Form.Item>

        <Form.Item name="note" label="备注">
          <TextArea rows={3} placeholder="其他需要记一笔的内容(自由文本)" />
        </Form.Item>

        <Divider />

        <Space>
          <Button type="primary" onClick={handleSubmit} loading={saving}>
            {isEdit ? '保存修改' : '保存'}
          </Button>
          <Button onClick={() => navigate('/requirements')}>取消</Button>
        </Space>
      </Form>
    </Card>
  )
}
