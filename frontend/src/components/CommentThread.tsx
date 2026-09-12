import { useCallback, useEffect, useState } from 'react'
import { App as AntApp, Avatar, Button, Input, Popconfirm, Space, Spin, Typography } from 'antd'
import { LikeFilled, LikeOutlined, DeleteOutlined } from '@ant-design/icons'
import {
  createComment,
  deleteComment,
  likeComment,
  listComments,
  unlikeComment,
} from '../api/resources'
import type { Comment } from '../types'
import { useAuth } from '../auth/AuthContext'
import { relativeTime } from '../format'

const BRAND = '#2464e4'

interface Props {
  /** content_types.py 里的 key:product / requirement(第五版还有 blog) */
  targetType: string
  targetId: number
}

/** 头像沿用顶栏那套:首字 + 品牌蓝。 */
function CommentAvatar({ name }: { name: string }) {
  return (
    <Avatar size="small" style={{ background: BRAND, flexShrink: 0 }}>
      {name.charAt(0) || '?'}
    </Avatar>
  )
}

/**
 * 评论串(第五版)。产品、需求、以后的博客共用 —— 宿主页只给 targetType / targetId,
 * 自己加载、自己重载,宿主页不管它的内部状态(契约照 ProductImagesCard)。
 *
 * 「删除」按钮的可见性只是**不显示一个注定 403 的按钮**,不是权限 ——
 * 后端每次都独立校验(见 app/permissions.py)。
 */
export default function CommentThread({ targetType, targetId }: Props) {
  const { message } = AntApp.useApp()
  const { user } = useAuth()

  const [comments, setComments] = useState<Comment[]>([])
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)

  // 正在回复哪条顶层评论(null = 没在回复)
  const [replyTo, setReplyTo] = useState<number | null>(null)
  const [replyBody, setReplyBody] = useState('')
  const [body, setBody] = useState('')

  const load = useCallback(async () => {
    setLoading(true)
    try {
      setComments(await listComments(targetType, targetId))
    } catch (err) {
      message.error(err instanceof Error ? err.message : '加载评论失败')
    } finally {
      setLoading(false)
    }
  }, [targetType, targetId, message])

  useEffect(() => {
    load()
  }, [load])

  const run = async (fn: () => Promise<unknown>, failText: string) => {
    setBusy(true)
    try {
      await fn()
      await load()
    } catch (err) {
      message.error(err instanceof Error ? err.message : failText)
    } finally {
      setBusy(false)
    }
  }

  const handleSubmit = async () => {
    const text = body.trim()
    if (!text) return
    await run(async () => {
      await createComment({ target_type: targetType, target_id: targetId, body: text })
      setBody('')
    }, '发表失败')
  }

  const handleReply = async (parentId: number) => {
    const text = replyBody.trim()
    if (!text) return
    await run(async () => {
      await createComment({
        target_type: targetType,
        target_id: targetId,
        body: text,
        parent_id: parentId,
      })
      setReplyBody('')
      setReplyTo(null)
    }, '回复失败')
  }

  const canDelete = (c: Comment) =>
    !c.deleted_at && (user?.role === 'admin' || c.author_id === user?.id)

  /** 一条评论的正文 + 操作栏。顶层和回复共用,`nested` 只影响缩进与「回复」按钮。 */
  const renderBody = (c: Comment, nested: boolean) => (
    <>
      <Space size={6} align="center" wrap>
        <Typography.Text strong>{c.author_name}</Typography.Text>
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          {relativeTime(c.created_at)}
        </Typography.Text>
      </Space>

      {c.deleted_at ? (
        <Typography.Text type="secondary" italic style={{ display: 'block' }}>
          该评论已删除
        </Typography.Text>
      ) : (
        <Typography.Paragraph style={{ whiteSpace: 'pre-wrap', margin: '4px 0 0' }}>
          {c.body}
        </Typography.Paragraph>
      )}

      <Space size={12} style={{ marginTop: 4 }}>
        {!c.deleted_at && (
          <Button
            type="text"
            size="small"
            disabled={busy}
            icon={c.liked_by_me ? <LikeFilled /> : <LikeOutlined />}
            style={{ color: c.liked_by_me ? BRAND : undefined }}
            onClick={() =>
              run(
                () => (c.liked_by_me ? unlikeComment(c.id) : likeComment(c.id)),
                '操作失败',
              )
            }
          >
            {c.like_count > 0 ? c.like_count : '赞'}
          </Button>
        )}
        {!nested && !c.deleted_at && (
          <Button
            type="text"
            size="small"
            disabled={busy}
            onClick={() => {
              setReplyTo(replyTo === c.id ? null : c.id)
              setReplyBody('')
            }}
          >
            回复
          </Button>
        )}
        {canDelete(c) && (
          <Popconfirm
            title="删除这条评论?"
            description="内容会被清空;它下面的回复会保留。"
            okText="删除"
            okButtonProps={{ danger: true }}
            onConfirm={() => run(() => deleteComment(c.id), '删除失败')}
          >
            <Button type="text" size="small" danger icon={<DeleteOutlined />} />
          </Popconfirm>
        )}
      </Space>
    </>
  )

  return (
    <Spin spinning={loading}>
      <Space direction="vertical" size={16} style={{ width: '100%' }}>
        <Input.TextArea
          rows={3}
          value={body}
          maxLength={5000}
          placeholder="写下你的点评…"
          onChange={(e) => setBody(e.target.value)}
        />
        <Button
          type="primary"
          loading={busy}
          disabled={!body.trim()}
          onClick={handleSubmit}
        >
          发表评论
        </Button>

        {comments.length === 0 && !loading && (
          <Typography.Text type="secondary">还没有评论,来说两句</Typography.Text>
        )}

        {comments.map((c) => (
          <div key={c.id} style={{ display: 'flex', gap: 10 }}>
            <CommentAvatar name={c.author_name} />
            <div style={{ flex: 1, minWidth: 0 }}>
              {renderBody(c, false)}

              {replyTo === c.id && (
                <Space direction="vertical" size={8} style={{ width: '100%', marginTop: 8 }}>
                  <Input.TextArea
                    rows={2}
                    autoFocus
                    value={replyBody}
                    maxLength={5000}
                    placeholder={`回复 ${c.author_name}…`}
                    onChange={(e) => setReplyBody(e.target.value)}
                  />
                  <Space>
                    <Button
                      type="primary"
                      size="small"
                      loading={busy}
                      disabled={!replyBody.trim()}
                      onClick={() => handleReply(c.id)}
                    >
                      回复
                    </Button>
                    <Button size="small" onClick={() => setReplyTo(null)}>
                      取消
                    </Button>
                  </Space>
                </Space>
              )}

              {/* 回复只有一层:这里不再提供「回复」按钮,后端也会 422 挡住 */}
              {c.replies.map((r) => (
                <div key={r.id} style={{ display: 'flex', gap: 10, marginTop: 12 }}>
                  <CommentAvatar name={r.author_name} />
                  <div style={{ flex: 1, minWidth: 0 }}>{renderBody(r, true)}</div>
                </div>
              ))}
            </div>
          </div>
        ))}
      </Space>
    </Spin>
  )
}
