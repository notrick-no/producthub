import { useEffect, useMemo, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { App as AntApp, Button, Input, Table, Tag, Typography } from 'antd'
import type { TableColumnsType } from 'antd'
import { PlusOutlined } from '@ant-design/icons'
import { listRequirements } from '../api/resources'
import type { Requirement } from '../types'
import {
  PRIORITY_COLOR,
  PRODUCT_TYPE_COLOR,
  REQUIREMENT_STATUS_COLOR,
} from '../requirementMeta'
import { formatMonthDay } from '../format'
import { useUrlParams } from '../useUrlParams'
import ListToolbar from '../components/ListToolbar'

/**
 * 需求记录列表:表格 + 搜索(?q=)。
 *
 * 列表的加载逻辑是从 ProductList 照抄来的 —— 第四版定稿:**先重复**。
 * 等会议列表页出现(第三处)再抽,那时候才看得出真正稳定的形状。
 * 搜索在前端过滤(需求数量在千级以内),所以不走后端查询参数。
 */
export default function RequirementList() {
  const navigate = useNavigate()
  const { params, setParam } = useUrlParams()
  const { message } = AntApp.useApp()

  const qRaw = params.get('q') ?? ''
  const q = qRaw.trim().toLowerCase()

  const [loading, setLoading] = useState(true)
  const [requirements, setRequirements] = useState<Requirement[]>([])

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    listRequirements()
      .then((data) => {
        if (!cancelled) setRequirements(data)
      })
      .catch((err) => {
        if (!cancelled) message.error(err instanceof Error ? err.message : '加载需求失败')
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [])

  const rows = useMemo(() => {
    if (!q) return requirements
    return requirements.filter(
      (r) =>
        r.description.toLowerCase().includes(q) || (r.detail ?? '').toLowerCase().includes(q),
    )
  }, [requirements, q])

  const columns: TableColumnsType<Requirement> = [
    {
      title: '需求描述',
      dataIndex: 'description',
      render: (_, r) => (
        <Link to={`/requirements/${r.id}`}>
          <Typography.Text strong>{r.description}</Typography.Text>
        </Link>
      ),
    },
    {
      title: '产品类型',
      dataIndex: 'product_type',
      width: 130,
      render: (_, r) =>
        r.product_type ? (
          <Tag color={PRODUCT_TYPE_COLOR[r.product_type]}>{r.product_type}</Tag>
        ) : null,
    },
    {
      title: '优先级',
      dataIndex: 'priority',
      width: 90,
      render: (_, r) =>
        r.priority ? <Tag color={PRIORITY_COLOR[r.priority]}>{r.priority}</Tag> : null,
    },
    {
      title: '进展状态',
      dataIndex: 'status',
      width: 110,
      render: (_, r) =>
        r.status ? <Tag color={REQUIREMENT_STATUS_COLOR[r.status]}>{r.status}</Tag> : null,
    },
    {
      title: '预计交付',
      dataIndex: 'due_on',
      // 比「更新时间」宽:这里显示完整的 2026-09-11(交付日期跨年,年份省不掉),
      // 而更新时间走 formatMonthDay 只有 9/11。110px 减去左右各 16px 的内边距
      // 只剩 78px,刚好卡在换行临界点上。
      width: 140,
      render: (_, r) => r.due_on ?? '—',
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
          <Button
            type="primary"
            icon={<PlusOutlined />}
            onClick={() => navigate('/requirements/new')}
          >
            新建需求
          </Button>
        }
      >
        <Input.Search
          allowClear
          placeholder="搜索需求描述 / 详情"
          style={{ maxWidth: 280 }}
          defaultValue={qRaw}
          onSearch={(v) => setParam('q', v.trim() || null)}
        />
      </ListToolbar>

      <Table<Requirement>
        rowKey="id"
        columns={columns}
        dataSource={rows}
        loading={loading}
        pagination={{ pageSize: 20, showTotal: (t) => `共 ${t} 条` }}
        locale={{ emptyText: q ? '没有匹配的需求' : '还没有需求,点「新建需求」开始记录' }}
        scroll={{ x: 790 }}
      />
    </>
  )
}
