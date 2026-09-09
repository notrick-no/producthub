import { useEffect, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import {
  App as AntApp,
  Button,
  Card,
  Popconfirm,
  Space,
  Spin,
  Tag,
  Typography,
} from 'antd'
import { ArrowLeftOutlined, DeleteOutlined, EditOutlined, GlobalOutlined } from '@ant-design/icons'
import { deleteProduct, getProduct } from '../api/resources'
import type { PriceTier, Product } from '../types'
import { STATUS_COLOR } from '../statusMeta'
import ProductImagesCard from '../components/ProductImagesCard'

const nf = new Intl.NumberFormat('zh-CN')

function formatDate(iso: string): string {
  return new Date(iso).toLocaleString('zh-CN', { dateStyle: 'medium', timeStyle: 'short' })
}

/** 一档价格显示成「金额/周期」;空金额 = 面议;0 = 免费。 */
function priceAmount(t: PriceTier): string {
  if (t.amount == null) return '面议/定制'
  if (t.amount === 0) return '免费'
  const cycle = t.cycle ? ` / ${t.cycle}` : ''
  return `${t.amount}${cycle}`
}

/** 研究内容块:统一卡片样式,保留换行。 */
function Section({ title, content }: { title: string; content?: string | null }) {
  return (
    <Card title={title} style={{ marginBottom: 16 }}>
      {content ? (
        <Typography.Paragraph style={{ whiteSpace: 'pre-wrap', margin: 0 }}>
          {content}
        </Typography.Paragraph>
      ) : (
        <Typography.Text type="secondary">暂无记录</Typography.Text>
      )}
    </Card>
  )
}

/** 产品详情页。 */
export default function ProductDetail() {
  const { id } = useParams()
  const navigate = useNavigate()
  const { message } = AntApp.useApp()

  const [product, setProduct] = useState<Product | null>(null)
  const [loading, setLoading] = useState(true)

  // 轻量刷新:素材增删/排序/改说明后调用,不闪整页 loading
  const refresh = () =>
    getProduct(Number(id))
      .then(setProduct)
      .catch(() => undefined)

  const load = () => {
    setLoading(true)
    getProduct(Number(id))
      .then(setProduct)
      .catch((err) => {
        message.error(err instanceof Error ? err.message : '加载产品失败')
        navigate('/', { replace: true })
      })
      .finally(() => setLoading(false))
  }

  useEffect(load, [id])

  const handleDelete = async () => {
    try {
      await deleteProduct(Number(id))
      message.success('已删除')
      navigate('/', { replace: true })
    } catch (err) {
      message.error(err instanceof Error ? err.message : '删除失败')
    }
  }

  if (loading) {
    return (
      <div style={{ textAlign: 'center', padding: 80 }}>
        <Spin tip="加载中…" />
      </div>
    )
  }
  if (!product) return null

  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      <Button type="text" icon={<ArrowLeftOutlined />} onClick={() => navigate('/')}>
        返回列表
      </Button>

      {/* 头部:名称 + 网址 + 标签 + 操作 */}
      <Card>
        <Space direction="vertical" size={8} style={{ width: '100%' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', gap: 16 }}>
            <Space align="start" size={12} wrap>
              <Typography.Title level={3} style={{ margin: 0 }}>
                {product.name}
              </Typography.Title>
              {product.url && (
                <Typography.Link href={product.url} target="_blank">
                  <GlobalOutlined /> {product.url}
                </Typography.Link>
              )}
            </Space>
            <Space>
              <Button icon={<EditOutlined />} onClick={() => navigate(`/products/${product.id}/edit`)}>
                编辑
              </Button>
              <Popconfirm
                title="删除这条记录?"
                description="删除后不可恢复,该产品上的分类标签也会一并移除。"
                okText="删除"
                okButtonProps={{ danger: true }}
                onConfirm={handleDelete}
              >
                <Button danger icon={<DeleteOutlined />}>
                  删除
                </Button>
              </Popconfirm>
            </Space>
          </div>

          <Space size={[4, 4]} wrap>
            {product.status && <Tag color={STATUS_COLOR[product.status]}>{product.status}</Tag>}
            {product.categories.length > 0 ? (
              product.categories.map((c) => (
                <Tag key={c.id} color="blue">
                  {c.name}
                </Tag>
              ))
            ) : (
              <Tag>未分类</Tag>
            )}
          </Space>

          <Space size={24} wrap>
            {product.founder && (
              <span>
                创始人: <Typography.Text>{product.founder}</Typography.Text>
              </span>
            )}
            <span>
              月活:{' '}
              <Typography.Text strong>
                {product.monthly_visits == null ? '—' : `${nf.format(product.monthly_visits)} 次/月`}
              </Typography.Text>
            </span>
          </Space>

          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            收录于 {formatDate(product.created_at)} · 更新于 {formatDate(product.updated_at)}
          </Typography.Text>
        </Space>
      </Card>

      <Card title="产品素材">
        <ProductImagesCard
          productId={product.id}
          images={product.images}
          onReload={refresh}
        />
      </Card>

      {product.price_tiers.length > 0 && (
        <Card title="分级定价">
          <Space direction="vertical" size={8} style={{ width: '100%' }}>
            {product.price_tiers.map((t) => (
              <div
                key={t.id}
                style={{
                  border: '1px solid #f0f0f0',
                  borderRadius: 8,
                  padding: '8px 12px',
                  display: 'flex',
                  justifyContent: 'space-between',
                  gap: 16,
                }}
              >
                <div>
                  <Typography.Text strong>{t.name || '未命名档'}</Typography.Text>
                  {t.note && (
                    <div>
                      <Typography.Text type="secondary" style={{ fontSize: 13 }}>
                        {t.note}
                      </Typography.Text>
                    </div>
                  )}
                </div>
                <Typography.Text strong style={{ whiteSpace: 'nowrap' }}>
                  {priceAmount(t)}
                </Typography.Text>
              </div>
            ))}
          </Space>
        </Card>
      )}

      <Section title="产品解决的问题" content={product.problem} />
      <Section title="网站的用户评价" content={product.user_reviews} />
      <Section title="网站的营销策略" content={product.marketing_strategy} />
      <Section title="产品技术分析" content={product.tech_analysis} />
    </Space>
  )
}
