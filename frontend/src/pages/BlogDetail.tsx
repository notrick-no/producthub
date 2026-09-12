import { useEffect, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import {
  App as AntApp,
  Alert,
  Button,
  Card,
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
  LikeFilled,
  LikeOutlined,
} from '@ant-design/icons'
import { deletePost, getPost, likePost, unlikePost, updatePost } from '../api/resources'
import type { BlogPost } from '../types'
import { BLOG_STATUS_TEXT } from '../types'
import { BLOG_STATUS_COLOR } from '../blogMeta'
import { formatDate } from '../format'
import { useAuth } from '../auth/AuthContext'
import CommentThread from '../components/CommentThread'

const BRAND = '#2464e4'

/** 帖子详情:标题 + 标签 + 正文 + 互动(点赞、点评)。 */
export default function BlogDetail() {
  const { id } = useParams()
  const navigate = useNavigate()
  const { message } = AntApp.useApp()
  const { user } = useAuth()

  const [post, setPost] = useState<BlogPost | null>(null)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    getPost(Number(id))
      .then((data) => {
        if (!cancelled) setPost(data)
      })
      .catch((err) => {
        if (cancelled) return
        // 别人的草稿后端给的是 404(不是 403)—— 这里也就只能照实说「不存在」,
        // 并把人送回列表,不解释为什么。
        message.error(err instanceof Error ? err.message : '加载帖子失败')
        navigate('/blog', { replace: true })
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [id])

  const reload = async () => {
    try {
      setPost(await getPost(Number(id)))
    } catch (err) {
      message.error(err instanceof Error ? err.message : '刷新失败')
    }
  }

  const run = async (fn: () => Promise<unknown>, failText: string) => {
    setBusy(true)
    try {
      await fn()
    } catch (err) {
      message.error(err instanceof Error ? err.message : failText)
    } finally {
      setBusy(false)
    }
  }

  const handlePublish = () =>
    run(async () => {
      await updatePost(Number(id), { status: 'published' })
      message.success('已发布')
      await reload()
    }, '发布失败')

  const handleDelete = async () => {
    try {
      await deletePost(Number(id))
      message.success('已删除')
      navigate('/blog', { replace: true })
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
  if (!post) return null

  // 前端只是不显示一个注定 403 的按钮,后端每次都独立校验
  // (见 app/permissions.py::can_edit_post)
  const canEdit = user?.role === 'admin' || (post.author_id !== null && post.author_id === user?.id)
  const isDraft = post.status === 'draft'

  // 「更新于」只在**看起来真的不一样**时才写。不能拿两个时间戳直接比字符串:
  // published_at 由 Python 侧 now_utc() 写,updated_at 由数据库 now() 写,
  // 发布那一刻两者差几微秒 —— 比原始值的话,刚发布的帖子会显示成
  //「发布于 10:32 · 更新于 10:32」。按显示粒度(到分钟)比就没这问题。
  const published = post.published_at ? formatDate(post.published_at) : null
  const updated = formatDate(post.updated_at)
  const showUpdated = published !== null && updated !== published

  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      <Button type="text" icon={<ArrowLeftOutlined />} onClick={() => navigate('/blog')}>
        返回列表
      </Button>

      <Card>
        <Space direction="vertical" size={8} style={{ width: '100%' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', gap: 16 }}>
            <Typography.Title level={3} style={{ margin: 0 }}>
              {post.title}
            </Typography.Title>
            <Space style={{ flex: 'none' }}>
              {canEdit && isDraft && (
                <Button type="primary" loading={busy} onClick={handlePublish}>
                  发布
                </Button>
              )}
              {canEdit && (
                <>
                  <Button icon={<EditOutlined />} onClick={() => navigate(`/blog/${post.id}/edit`)}>
                    编辑
                  </Button>
                  <Popconfirm
                    title="删除这篇帖子?"
                    description="删除后不可恢复,它下面的点评与点赞也会一并消失。"
                    okText="删除"
                    okButtonProps={{ danger: true }}
                    onConfirm={handleDelete}
                  >
                    <Button danger icon={<DeleteOutlined />}>
                      删除
                    </Button>
                  </Popconfirm>
                </>
              )}
            </Space>
          </div>

          <Space size={[4, 4]} wrap>
            <Tag color={BLOG_STATUS_COLOR[post.status]}>{BLOG_STATUS_TEXT[post.status]}</Tag>
            {post.tags.map((t) => (
              <Tag key={t.id} color="blue">
                {t.name}
              </Tag>
            ))}
          </Space>

          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            {post.author_name}
            {published ? ` · 发布于 ${published}` : ` · 创建于 ${formatDate(post.created_at)}`}
            {showUpdated ? ` · 更新于 ${updated}` : ''}
          </Typography.Text>

          {/* 点赞:草稿不显示 —— 给一篇还没露面的东西点赞没有意义,
              而且那条「赞」在它发布前谁也看不见 */}
          {!isDraft && (
            <Button
              disabled={busy}
              icon={post.liked_by_me ? <LikeFilled /> : <LikeOutlined />}
              // 品牌色而不是 antd 默认蓝:全站的「已选中」都用 Logo 那个蓝
              // (顶栏头像、评论点赞同款),这里是同一件事
              style={post.liked_by_me ? { color: BRAND } : undefined}
              onClick={() =>
                run(async () => {
                  await (post.liked_by_me ? unlikePost(post.id) : likePost(post.id))
                  await reload()
                }, '操作失败')
              }
            >
              {post.like_count > 0 ? `${post.like_count} 人赞过` : '点赞'}
            </Button>
          )}
        </Space>
      </Card>

      {isDraft && (
        <Alert
          type="info"
          showIcon
          message="这是一篇草稿"
          description="只有你和管理员看得到它。发布之后才会出现在博客列表、首页动态与汇总里,也才能被点评。"
        />
      )}

      <Card>
        {post.body ? (
          <Typography.Paragraph style={{ whiteSpace: 'pre-wrap', margin: 0 }}>
            {post.body}
          </Typography.Paragraph>
        ) : (
          <Typography.Text type="secondary">还没有正文</Typography.Text>
        )}
      </Card>

      {/* 点评留在最后:它是互动,不是这篇帖子本身。
          草稿不挂评论区 —— 后端对草稿的评论接口是 404(草稿压根不算内容),
          挂上去只会让人点了报错。等发布,这一块自己就出来了。 */}
      {!isDraft && (
        <Card title="点评">
          <CommentThread targetType="blog" targetId={post.id} />
        </Card>
      )}
    </Space>
  )
}
