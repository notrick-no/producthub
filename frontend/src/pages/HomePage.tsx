import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { App as AntApp, Card, List, Space, Tag, Typography } from 'antd'
import { fetchSummary, listActivity } from '../api/resources'
import type { ActivityAction, ActivityEvent, SummaryItem } from '../types'
import PhilosophyHero from '../components/PhilosophyHero'

const ACTION_TEXT: Record<ActivityAction, string> = {
  create: '新建了',
  update: '更新了',
  delete: '删除了',
}

/** 相对时间:刚刚 / N 分钟前 / N 小时前 / N 天前,超过一周显示日期。 */
function relativeTime(iso: string): string {
  const minutes = Math.floor((Date.now() - new Date(iso).getTime()) / 60000)
  if (minutes < 1) return '刚刚'
  if (minutes < 60) return `${minutes} 分钟前`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours} 小时前`
  const days = Math.floor(hours / 24)
  if (days < 7) return `${days} 天前`
  return new Date(iso).toLocaleDateString('zh-CN', { month: 'numeric', day: 'numeric' })
}

/**
 * 首页:最近动态 + 项目汇总。
 *
 * 两块都由后端给结构、前端照单渲染:
 *   - 汇总直接 map `/api/summary` 返回的数组,**不写死有哪几种内容类型** ——
 *     以后加了会议 / 博客,后端多返回一项,这里自动多一张卡,前端零改动。
 *   - 动态的跳转地址也是后端按内容类型拼好的,前端不拼路径。
 */
export default function HomePage() {
  const { message } = AntApp.useApp()

  const [loading, setLoading] = useState(true)
  const [events, setEvents] = useState<ActivityEvent[]>([])
  const [summary, setSummary] = useState<SummaryItem[]>([])

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    Promise.all([listActivity(), fetchSummary()])
      .then(([activity, items]) => {
        if (cancelled) return
        setEvents(activity)
        setSummary(items)
      })
      .catch((err) => {
        if (!cancelled) message.error(err instanceof Error ? err.message : '加载首页失败')
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [])

  return (
    <>
      <PhilosophyHero />

      <Typography.Title level={4} style={{ marginTop: 24 }}>
        最近动态
      </Typography.Title>
      <List
        loading={loading}
        dataSource={events}
        locale={{ emptyText: '还没有动态。新建一条产品记录或需求,就会出现在这里' }}
        style={{ background: '#fff', borderRadius: 8 }}
        renderItem={(e) => {
          const line = (
            <Space size={8} wrap>
              <Tag color="blue" style={{ marginInlineEnd: 0 }}>
                {e.content_type_name}
              </Tag>
              <Typography.Text type="secondary">{e.actor_name}</Typography.Text>
              <Typography.Text>{ACTION_TEXT[e.action]}</Typography.Text>
              <Typography.Text strong>{`「${e.title}」`}</Typography.Text>
            </Space>
          )
          return (
            <List.Item>
              <div
                style={{
                  display: 'flex',
                  justifyContent: 'space-between',
                  alignItems: 'center',
                  gap: 12,
                  width: '100%',
                }}
              >
                {/* 删除事件没有 url(对象已经没了),渲染成纯文本,不给点出 404 的链接 */}
                {e.url ? <Link to={e.url}>{line}</Link> : line}
                <Typography.Text type="secondary" style={{ fontSize: 12, flex: 'none' }}>
                  {relativeTime(e.created_at)}
                </Typography.Text>
              </div>
            </List.Item>
          )
        }}
      />

      <Typography.Title level={4} style={{ marginTop: 24 }}>
        项目汇总
      </Typography.Title>
      <Space size={12} wrap>
        {summary.map((item) => (
          <Link key={item.key} to={item.url}>
            <Card size="small" hoverable style={{ minWidth: 150 }}>
              <Typography.Text type="secondary">{item.name}</Typography.Text>
              <div style={{ fontSize: 28, fontWeight: 600, lineHeight: 1.3 }}>{item.count}</div>
            </Card>
          </Link>
        ))}
      </Space>
    </>
  )
}
