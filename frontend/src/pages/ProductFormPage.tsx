import { useEffect, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import {
  App as AntApp,
  Button,
  Card,
  Divider,
  Form,
  Input,
  InputNumber,
  Select,
  Space,
  Spin,
  Typography,
} from 'antd'
import { PlusOutlined } from '@ant-design/icons'
import { createCategory, createProduct, getProduct, listCategories, updateProduct } from '../api/resources'
import type { Category, ProductPayload, ProductStatus } from '../types'
import { PRODUCT_STATUSES } from '../types'
import MilestoneEditor, { collectMilestones, milestoneToDraft } from '../components/MilestoneEditor'
import type { MilestoneDraft } from '../components/MilestoneEditor'
import PriceTierEditor, { collectPriceTiers, priceTierToDraft } from '../components/PriceTierEditor'
import type { PriceTierDraft } from '../components/PriceTierEditor'

const { TextArea } = Input

interface FormValues {
  name: string
  url?: string
  founder?: string
  monthly_visits?: number | null
  status?: ProductStatus | null
  problem?: string
  user_reviews?: string
  marketing_strategy?: string
  tech_analysis?: string
  category_ids?: number[]
}

/** 把表单值规整成后端可接受的 payload(空串 → null)。 */
function toPayload(values: FormValues): ProductPayload {
  const text = (v?: string) => (v == null ? null : v.trim() || null)
  return {
    name: values.name.trim(),
    url: text(values.url),
    founder: text(values.founder),
    monthly_visits: values.monthly_visits ?? null,
    status: values.status ?? null,
    problem: text(values.problem),
    user_reviews: text(values.user_reviews),
    marketing_strategy: text(values.marketing_strategy),
    tech_analysis: text(values.tech_analysis),
    category_ids: values.category_ids ?? [],
  }
}

/** 新建(/products/new)与编辑(/products/:id/edit)共用的产品表单页。 */
export default function ProductFormPage() {
  const { id } = useParams()
  const isEdit = id !== undefined
  const navigate = useNavigate()
  const { message } = AntApp.useApp()

  const [form] = Form.useForm<FormValues>()
  const [categories, setCategories] = useState<Category[]>([])
  const [loadingProduct, setLoadingProduct] = useState(isEdit)
  const [saving, setSaving] = useState(false)
  const [milestones, setMilestones] = useState<MilestoneDraft[]>([])
  const [priceTiers, setPriceTiers] = useState<PriceTierDraft[]>([])

  // 下拉里"直接新建分类"用的输入框
  const [newCatName, setNewCatName] = useState('')

  useEffect(() => {
    listCategories()
      .then(setCategories)
      .catch((err) => message.error(err instanceof Error ? err.message : '加载分类失败'))
  }, [])

  // 编辑模式:预填现有数据
  useEffect(() => {
    if (!isEdit) return
    getProduct(Number(id))
      .then((p) => {
        form.setFieldsValue({
          name: p.name,
          url: p.url ?? '',
          founder: p.founder ?? '',
          monthly_visits: p.monthly_visits,
          status: p.status ?? null,
          problem: p.problem ?? '',
          user_reviews: p.user_reviews ?? '',
          marketing_strategy: p.marketing_strategy ?? '',
          tech_analysis: p.tech_analysis ?? '',
          category_ids: p.categories.map((c) => c.id),
        })
        setMilestones(p.milestones.map(milestoneToDraft))
        setPriceTiers(p.price_tiers.map(priceTierToDraft))
      })
      .catch((err) => {
        message.error(err instanceof Error ? err.message : '加载产品失败')
        navigate('/', { replace: true })
      })
      .finally(() => setLoadingProduct(false))
  }, [isEdit, id])

  const handleCreateCategoryInline = async () => {
    const name = newCatName.trim()
    if (!name) return
    try {
      const created = await createCategory({ name })
      setCategories((prev) => [...prev, created])
      // 把新分类并入当前已选
      const current = form.getFieldValue('category_ids') ?? []
      form.setFieldsValue({ category_ids: [...current, created.id] })
      setNewCatName('')
    } catch (err) {
      message.error(err instanceof Error ? err.message : '创建分类失败')
    }
  }

  const handleSubmit = async () => {
    let values: FormValues
    try {
      values = await form.validateFields()
    } catch {
      return // 校验失败,antd 已标红
    }

    const collected = collectMilestones(milestones)
    if (collected.error) {
      message.error('发展历程里有没填完的行:请补全「时间 + 事件名」,或删除该行')
      return
    }

    setSaving(true)
    const payload = toPayload(values)
    payload.milestones = collected.milestones ?? []
    payload.price_tiers = collectPriceTiers(priceTiers).price_tiers ?? []
    try {
      const saved = isEdit ? await updateProduct(Number(id), payload) : await createProduct(payload)
      message.success(isEdit ? '已保存修改' : `已创建「${saved.name}」`)
      navigate('/', { replace: true }) // 详情页做好后改为跳详情
    } catch (err) {
      message.error(err instanceof Error ? err.message : '保存失败')
    } finally {
      setSaving(false)
    }
  }

  if (loadingProduct) {
    return (
      <div style={{ textAlign: 'center', padding: 80 }}>
        <Spin tip="加载中…" />
      </div>
    )
  }

  return (
    <Card title={isEdit ? '编辑记录' : '新建记录'} style={{ maxWidth: 760 }}>
      <Form<FormValues>
        form={form}
        layout="vertical"
        onFinish={handleSubmit}
        initialValues={{ category_ids: [], status: '调研中' }}
      >
        <Typography.Title level={5}>基础信息</Typography.Title>
        <Form.Item
          name="name"
          label="产品名"
          rules={[{ required: true, whitespace: true, message: '请填写产品名' }]}
        >
          <Input placeholder="如: Notion" maxLength={255} />
        </Form.Item>

        <Form.Item name="url" label="网址">
          <Input placeholder="https:// 或直接域名,如 notion.so(可不带协议)" maxLength={2048} />
        </Form.Item>

        <Form.Item name="monthly_visits" label="月活量">
          <InputNumber
            style={{ width: 240 }}
            min={0}
            precision={0}
            placeholder="月访问量(整数)"
            addonAfter="次/月"
          />
        </Form.Item>

        <Form.Item name="status" label="产品状态" tooltip="空 = 未设置(调研早期)">
          <Select
            allowClear
            style={{ width: 240 }}
            placeholder="选择当前状态"
            options={PRODUCT_STATUSES.map((s) => ({ value: s, label: s }))}
          />
        </Form.Item>

        <Form.Item name="founder" label="创始人信息">
          <TextArea rows={2} placeholder="创始人姓名、简介等(自由文本)" />
        </Form.Item>

        <Divider />

        <Typography.Title level={5}>研究内容</Typography.Title>

        <Form.Item name="problem" label="产品解决的问题">
          <TextArea rows={4} placeholder="这个产品解决了什么问题?给谁解决?" />
        </Form.Item>

        <Form.Item name="user_reviews" label="网站的用户评价">
          <TextArea rows={6} placeholder="整理的用户/媒体评价、口碑如何" />
        </Form.Item>

        <Form.Item name="marketing_strategy" label="网站的营销策略">
          <TextArea rows={6} placeholder="它的获客/增长/营销打法" />
        </Form.Item>

        <Form.Item name="tech_analysis" label="产品技术分析">
          <TextArea rows={4} placeholder="技术栈、架构、关键实现方式等" />
        </Form.Item>

        <Divider />

        <Typography.Title level={5}>发展历程</Typography.Title>
        <Typography.Paragraph type="secondary" style={{ marginTop: -4 }}>
          产品关键时间点,按时间先后展示。例如:产品成立 / 上线 MVP / 开始收费。
        </Typography.Paragraph>
        <MilestoneEditor drafts={milestones} onChange={setMilestones} />

        <Divider />

        <Typography.Title level={5}>分级定价</Typography.Title>
        <Typography.Paragraph type="secondary" style={{ marginTop: -4 }}>
          分档记录价格:金额留空表示面议/定制,填 0 表示免费;币种可写在备注里。
        </Typography.Paragraph>
        <PriceTierEditor drafts={priceTiers} onChange={setPriceTiers} />

        <Form.Item name="category_ids" label="所属分类">
          <Select
            mode="multiple"
            allowClear
            placeholder="选择分类,也可在下拉里直接新建"
            options={categories.map((c) => ({ value: c.id, label: c.name }))}
            dropdownRender={(menu) => (
              <>
                {menu}
                <Divider style={{ margin: '8px 0' }} />
                <Space style={{ padding: '0 8px 4px' }} wrap>
                  <Input
                    size="small"
                    placeholder="新分类名"
                    style={{ width: 180 }}
                    value={newCatName}
                    maxLength={100}
                    onChange={(e) => setNewCatName(e.target.value)}
                    onPressEnter={handleCreateCategoryInline}
                  />
                  <Button size="small" icon={<PlusOutlined />} onClick={handleCreateCategoryInline}>
                    新建
                  </Button>
                </Space>
              </>
            )}
          />
        </Form.Item>

        <Divider />

        <Space>
          <Button type="primary" onClick={handleSubmit} loading={saving}>
            {isEdit ? '保存修改' : '保存'}
          </Button>
          <Button onClick={() => navigate('/')}>取消</Button>
        </Space>
      </Form>
    </Card>
  )
}
