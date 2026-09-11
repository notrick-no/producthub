import { useEffect, useMemo, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { App as AntApp, Button, Input, Select, Space, Table, Tag, Typography } from 'antd'
import type { TableColumnsType } from 'antd'
import { PlusOutlined, SettingOutlined } from '@ant-design/icons'
import { listCategories, listProducts } from '../api/resources'
import type { Category, Product } from '../types'
import { PRODUCT_STATUS_COLOR } from '../productMeta'
import { formatMonthDay } from '../format'
import { useUrlParams } from '../useUrlParams'
import CategoryManageModal from '../components/CategoryManageModal'
import ListToolbar from '../components/ListToolbar'

const nf = new Intl.NumberFormat('zh-CN')

function formatVisits(v?: number | null): string {
  return v == null ? '—' : nf.format(v)
}

/**
 * 产品分析:表格 + 分类筛选(?cat=) + 搜索(?q=)。
 *
 * 第四版把分类从侧栏挪到了页内下拉 —— 筛选状态仍然只存在 URL 里,
 * 所以刷新、分享链接、浏览器后退都照旧能还原列表。
 */
export default function ProductList() {
  const navigate = useNavigate()
  const { params, setParam } = useUrlParams()
  const { message } = AntApp.useApp()

  const cat = params.get('cat') ?? 'all'
  const categoryId = cat !== 'all' ? Number(cat) : undefined
  const qRaw = params.get('q') ?? ''
  const q = qRaw.trim().toLowerCase()

  const [loading, setLoading] = useState(true)
  const [products, setProducts] = useState<Product[]>([])
  const [categories, setCategories] = useState<Category[]>([])
  const [manageOpen, setManageOpen] = useState(false)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    listProducts(categoryId)
      .then((data) => {
        if (!cancelled) setProducts(data)
      })
      .catch((err) => {
        if (!cancelled) message.error(err instanceof Error ? err.message : '加载产品失败')
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [categoryId])

  /** 拉分类下拉的选项;若正在筛选的分类已被删掉,自动退回「全部」。 */
  const loadCategories = async () => {
    try {
      const list = await listCategories()
      setCategories(list)
      if (categoryId !== undefined && !list.some((c) => c.id === categoryId)) {
        setParam('cat', null)
      }
    } catch {
      message.error('加载分类失败')
    }
  }

  // 进页面拉一次;之后分类的增删改名由「管理分类」弹窗的回调触发重拉
  useEffect(() => {
    loadCategories()
  }, [])

  const rows = useMemo(() => {
    if (!q) return products
    return products.filter(
      (p) => p.name.toLowerCase().includes(q) || (p.url ?? '').toLowerCase().includes(q),
    )
  }, [products, q])

  const columns: TableColumnsType<Product> = [
    {
      title: '名称',
      dataIndex: 'name',
      render: (_, r) => (
        <Space direction="vertical" size={0}>
          <Link to={`/products/${r.id}`}>
            <Typography.Text strong>{r.name}</Typography.Text>
          </Link>
          {r.url && (
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              {r.url}
            </Typography.Text>
          )}
        </Space>
      ),
    },
    {
      title: '分类',
      dataIndex: 'categories',
      width: 200,
      render: (_, r) => (
        <Space size={[4, 4]} wrap>
          {r.categories.map((c) => (
            <Tag key={c.id} color="blue">
              {c.name}
            </Tag>
          ))}
        </Space>
      ),
    },
    {
      title: '状态',
      dataIndex: 'status',
      width: 96,
      render: (_, r) =>
        r.status ? <Tag color={PRODUCT_STATUS_COLOR[r.status]}>{r.status}</Tag> : null,
    },
    {
      title: '月活',
      dataIndex: 'monthly_visits',
      width: 130,
      align: 'right',
      sorter: (a, b) => (a.monthly_visits ?? -1) - (b.monthly_visits ?? -1),
      render: (_, r) => formatVisits(r.monthly_visits),
    },
    {
      title: '更新时间',
      dataIndex: 'updated_at',
      width: 110,
      sorter: (a, b) => a.updated_at.localeCompare(b.updated_at),
      render: (_, r) => formatMonthDay(r.updated_at),
    },
  ]

  return (
    <>
      <ListToolbar
        actions={
          <Button type="primary" icon={<PlusOutlined />} onClick={() => navigate('/products/new')}>
            新建记录
          </Button>
        }
      >
        <Select
          value={cat}
          onChange={(v) => setParam('cat', v === 'all' ? null : v)}
          style={{ minWidth: 160 }}
          options={[
            { value: 'all', label: '全部分类' },
            ...categories.map((c) => ({ value: String(c.id), label: c.name })),
          ]}
        />
        <Input.Search
          allowClear
          placeholder="搜索产品名 / 网址"
          style={{ maxWidth: 280 }}
          defaultValue={qRaw}
          onSearch={(v) => setParam('q', v.trim() || null)}
        />
        <Button type="text" icon={<SettingOutlined />} onClick={() => setManageOpen(true)}>
          管理分类
        </Button>
      </ListToolbar>

      <Table<Product>
        rowKey="id"
        columns={columns}
        dataSource={rows}
        loading={loading}
        pagination={{ pageSize: 20, showTotal: (t) => `共 ${t} 条` }}
        locale={{ emptyText: q ? '没有匹配的产品' : '还没有产品,点「新建记录」开始研究' }}
        scroll={{ x: 720 }}
      />

      <CategoryManageModal
        open={manageOpen}
        onClose={() => setManageOpen(false)}
        onChanged={loadCategories}
      />
    </>
  )
}
