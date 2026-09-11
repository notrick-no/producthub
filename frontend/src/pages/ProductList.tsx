import { useEffect, useMemo, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { App as AntApp, Button, Input, Select, Space, Table, Tag, Typography } from 'antd'
import type { TableColumnsType } from 'antd'
import { GlobalOutlined, PlusOutlined, SettingOutlined } from '@ant-design/icons'
import { listCategories, listProducts } from '../api/resources'
import type { Category, Product } from '../types'
import { PRODUCT_STATUS_COLOR } from '../productMeta'
import { externalHref } from '../externalLink'
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
        <Link to={`/products/${r.id}`} onClick={(e) => e.stopPropagation()}>
          <Typography.Text strong>{r.name}</Typography.Text>
        </Link>
      ),
    },
    {
      title: '域名',
      dataIndex: 'url',
      // 单独一列而不是跟在名称底下:它是个能点出去的外链,和「进详情」是两件事,
      // 混在同一个单元格里容易点错。宽度给够但仍会截断,长网址不撑破表格。
      width: 220,
      ellipsis: true,
      render: (_, r) =>
        r.url ? (
          <Typography.Link
            href={externalHref(r.url)}
            target="_blank"
            // 不让它冒泡:点域名是去网站,不是进详情页
            onClick={(e) => e.stopPropagation()}
          >
            <GlobalOutlined /> {r.url}
          </Typography.Link>
        ) : null,
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

      {/* 整行可点:点空白处也能进详情,不用非得瞄准产品名。行内自带动作的元素
          (域名外链)各自 stopPropagation,不会连带跳进详情页。 */}
      <Table<Product>
        rowKey="id"
        columns={columns}
        dataSource={rows}
        loading={loading}
        pagination={{ pageSize: 20, showTotal: (t) => `共 ${t} 条` }}
        locale={{ emptyText: q ? '没有匹配的产品' : '还没有产品,点「新建记录」开始研究' }}
        scroll={{ x: 940 }}
        onRow={(r) => ({
          style: { cursor: 'pointer' },
          onClick: () => {
            // 拖选一段文字时不该跳转 —— 否则选中想复制的内容,手一松页面就被带走了
            if (window.getSelection()?.toString()) return
            navigate(`/products/${r.id}`)
          },
        })}
      />

      <CategoryManageModal
        open={manageOpen}
        onClose={() => setManageOpen(false)}
        onChanged={loadCategories}
      />
    </>
  )
}
