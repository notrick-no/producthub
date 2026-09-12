import { useEffect, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import {
  App as AntApp,
  Button,
  Card,
  Descriptions,
  Popconfirm,
  Space,
  Spin,
  Tag,
  Typography,
} from 'antd'
import {
  ArrowLeftOutlined,
  DeleteOutlined,
  EditOutlined,
  LinkOutlined,
} from '@ant-design/icons'
import { deleteRequirement, getRequirement } from '../api/resources'
import type { Requirement } from '../types'
import {
  PRIORITY_COLOR,
  PRODUCT_TYPE_COLOR,
  REQUIREMENT_STATUS_COLOR,
  SOURCE_COLOR,
} from '../requirementMeta'
import { externalHref } from '../externalLink'
import { formatDate } from '../format'
import CommentThread from '../components/CommentThread'

/** 需求详情页:标题 + 标签 + 详情长文 + 其余字段。 */
export default function RequirementDetail() {
  const { id } = useParams()
  const navigate = useNavigate()
  const { message } = AntApp.useApp()

  const [requirement, setRequirement] = useState<Requirement | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    getRequirement(Number(id))
      .then((data) => {
        if (!cancelled) setRequirement(data)
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
  }, [id])

  const handleDelete = async () => {
    try {
      await deleteRequirement(Number(id))
      message.success('已删除')
      navigate('/requirements', { replace: true })
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
  if (!requirement) return null

  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      <Button type="text" icon={<ArrowLeftOutlined />} onClick={() => navigate('/requirements')}>
        返回列表
      </Button>

      <Card>
        <Space direction="vertical" size={8} style={{ width: '100%' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', gap: 16 }}>
            <Typography.Title level={3} style={{ margin: 0 }}>
              {requirement.description}
            </Typography.Title>
            <Space style={{ flex: 'none' }}>
              <Button
                icon={<EditOutlined />}
                onClick={() => navigate(`/requirements/${requirement.id}/edit`)}
              >
                编辑
              </Button>
              <Popconfirm
                title="删除这条需求?"
                description="删除后不可恢复。首页动态里会留下一条删除记录。"
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
            {requirement.product_type && (
              <Tag color={PRODUCT_TYPE_COLOR[requirement.product_type]}>
                {requirement.product_type}
              </Tag>
            )}
            {requirement.priority && (
              <Tag color={PRIORITY_COLOR[requirement.priority]}>优先级 {requirement.priority}</Tag>
            )}
            {requirement.status && (
              <Tag color={REQUIREMENT_STATUS_COLOR[requirement.status]}>
                {requirement.status}
              </Tag>
            )}
          </Space>

          {requirement.link_url && (
            <Typography.Link href={externalHref(requirement.link_url)} target="_blank">
              <LinkOutlined /> {requirement.link_url}
            </Typography.Link>
          )}

          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            创建于 {formatDate(requirement.created_at)} · 更新于 {formatDate(requirement.updated_at)}
          </Typography.Text>
        </Space>
      </Card>

      <Card title="需求详情">
        {requirement.detail ? (
          <Typography.Paragraph style={{ whiteSpace: 'pre-wrap', margin: 0 }}>
            {requirement.detail}
          </Typography.Paragraph>
        ) : (
          <Typography.Text type="secondary">暂无记录</Typography.Text>
        )}
      </Card>

      <Card title="其他信息">
        <Descriptions
          column={{ xs: 1, sm: 2 }}
          size="small"
          items={[
            {
              key: 'source',
              label: '需求来源',
              children: requirement.source ? (
                <Tag color={SOURCE_COLOR[requirement.source]}>{requirement.source}</Tag>
              ) : (
                '—'
              ),
            },
            {
              key: 'proposed_on',
              label: '提出日期',
              children: requirement.proposed_on ?? '—',
            },
            {
              key: 'estimated_days',
              label: '预估投入',
              children:
                requirement.estimated_days == null ? '—' : `${requirement.estimated_days} 天`,
            },
            {
              key: 'due_on',
              label: '预计交付日期',
              children: requirement.due_on ?? '—',
            },
            {
              key: 'note',
              label: '备注',
              span: 2,
              children: requirement.note ? (
                <Typography.Paragraph style={{ whiteSpace: 'pre-wrap', margin: 0 }}>
                  {requirement.note}
                </Typography.Paragraph>
              ) : (
                '—'
              ),
            },
          ]}
        />
      </Card>

      {/* 点评留在最后:它是互动,不是这条需求本身 */}
      <Card title="点评">
        <CommentThread targetType="requirement" targetId={requirement.id} />
      </Card>
    </Space>
  )
}
