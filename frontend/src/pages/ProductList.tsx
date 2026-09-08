import { useEffect, useMemo, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { App as AntApp, Space, Table, Tag, Typography } from 'antd'
import type { TableColumnsType } from 'antd'
import { listProducts } from '../api/resources'
import type { Product } from '../types'

const nf = new Intl.NumberFormat('zh-CN')

function formatVisits(v?: number | null): string {
  return v == null ? '—' : nf.format(v)
}

function formatTime(iso: string): string {
  const d = new Date(iso)
  return d.toLocaleDateString('zh-CN', { month: 'numeric', day: 'numeric' })
}

/** 产品列表:表格 + 分类筛选(?cat=) + 搜索(?q=) */
export default function ProductList() {
  const [params] = useSearchParams()
  const { message } = AntApp.useApp()

  const categoryId = (() => {
    const v = params.get('cat')
    return v && v !== 'all' ? Number(v) : undefined
  })()
  const q = (params.get('q') ?? '').trim().toLowerCase()

  const [loading, setLoading] = useState(true)
  const [products, setProducts] = useState<Product[]>([])

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
      render: (_, r) => formatTime(r.updated_at),
    },
  ]

  return (
    <Table<Product>
      rowKey="id"
      columns={columns}
      dataSource={rows}
      loading={loading}
      pagination={{ pageSize: 20, showTotal: (t) => `共 ${t} 条` }}
      locale={{ emptyText: q ? '没有匹配的产品' : '还没有产品,点右上角「新建记录」开始研究' }}
      scroll={{ x: 720 }}
    />
  )
}
