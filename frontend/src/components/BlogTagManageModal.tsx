import { useEffect, useState } from 'react'
import {
  App as AntApp,
  Button,
  Empty,
  Input,
  List,
  Modal,
  Popconfirm,
  Space,
  Spin,
  Tooltip,
  Typography,
} from 'antd'
import { DeleteOutlined, EditOutlined } from '@ant-design/icons'
import { deleteBlogTag, listBlogTags, updateBlogTag } from '../api/resources'
import type { BlogTag } from '../types'

interface Props {
  open: boolean
  onClose: () => void
  /** 改名 / 删除成功后通知父级,让它刷新标签下拉与列表 */
  onChanged: () => void
}

/**
 * 管理博客标签弹窗:列出全部标签,支持改名 / 删除。形状照 CategoryManageModal。
 *
 * **这里不显示「N 篇帖子」那个用量数字**(分类弹窗是显示的)。原因:帖子列表对
 * 不同的人是不同的 —— 别人的草稿根本不在员工的列表里,拿 listPosts() 数出来的
 * 用量对员工是 0、对管理员是 3,同一个标签两个数。分类没有这个问题(产品全公开)。
 * 一个会看人变脸的计数不如不给:删除确认里那句「帖子保留,仅移除标签」才是要说的。
 */
export default function BlogTagManageModal({ open, onClose, onChanged }: Props) {
  const { message } = AntApp.useApp()

  const [tags, setTags] = useState<BlogTag[]>([])
  const [loading, setLoading] = useState(false)

  // 正在重命名的标签(null = 未在改名)
  const [renaming, setRenaming] = useState<BlogTag | null>(null)
  const [renameValue, setRenameValue] = useState('')
  const [savingRename, setSavingRename] = useState(false)

  useEffect(() => {
    if (!open) return
    let cancelled = false
    setLoading(true)
    listBlogTags()
      .then((list) => {
        if (!cancelled) setTags(list)
      })
      .catch((err) => {
        if (!cancelled) message.error(err instanceof Error ? err.message : '加载标签失败')
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [open])

  const startRename = (t: BlogTag) => {
    setRenaming(t)
    setRenameValue(t.name)
  }

  const handleRename = async () => {
    if (!renaming) return
    const name = renameValue.trim()
    if (!name || name === renaming.name) {
      setRenaming(null)
      return
    }
    setSavingRename(true)
    try {
      await updateBlogTag(renaming.id, { name })
      message.success(`已改名为「${name}」`)
      // 本地同步 + 通知父级刷新标签下拉
      setTags((prev) => prev.map((t) => (t.id === renaming.id ? { ...t, name } : t)))
      onChanged()
      setRenaming(null)
    } catch (err) {
      message.error(err instanceof Error ? err.message : '重命名失败')
    } finally {
      setSavingRename(false)
    }
  }

  const handleDelete = async (t: BlogTag) => {
    try {
      await deleteBlogTag(t.id)
      message.success(`已删除「${t.name}」`)
      setTags((prev) => prev.filter((x) => x.id !== t.id))
      onChanged()
    } catch (err) {
      message.error(err instanceof Error ? err.message : '删除失败')
    }
  }

  return (
    <>
      <Modal
        title="管理标签"
        open={open}
        onCancel={onClose}
        width={560}
        footer={
          <Button type="primary" onClick={onClose}>
            完成
          </Button>
        }
      >
        {loading ? (
          <div style={{ textAlign: 'center', padding: 40 }}>
            <Spin />
          </div>
        ) : tags.length === 0 ? (
          <Empty description="还没有标签。可在写帖子时顺手创建" />
        ) : (
          <>
            <Typography.Paragraph type="secondary" style={{ marginBottom: 8 }}>
              改名或删除标签。删除后帖子保留,仅移除它的这个标签。
            </Typography.Paragraph>
            <List
              dataSource={tags}
              rowKey="id"
              renderItem={(t) => (
                <List.Item style={{ paddingInline: 0 }}>
                  <div
                    style={{
                      display: 'flex',
                      justifyContent: 'space-between',
                      alignItems: 'center',
                      width: '100%',
                      gap: 12,
                    }}
                  >
                    <Typography.Text strong>{t.name}</Typography.Text>
                    <Space size={4}>
                      <Tooltip title="重命名">
                        <Button
                          type="text"
                          icon={<EditOutlined />}
                          onClick={() => startRename(t)}
                        />
                      </Tooltip>
                      <Popconfirm
                        title={`删除标签「${t.name}」?`}
                        description="用这个标签的帖子会保留,只是不再带它。"
                        okText="删除"
                        okButtonProps={{ danger: true }}
                        onConfirm={() => handleDelete(t)}
                      >
                        <Tooltip title="删除">
                          <Button type="text" danger icon={<DeleteOutlined />} />
                        </Tooltip>
                      </Popconfirm>
                    </Space>
                  </div>
                </List.Item>
              )}
            />
          </>
        )}
      </Modal>

      {/* 重命名小窗:复用新建标签的输入样式 */}
      <Modal
        title="重命名标签"
        open={renaming !== null}
        onCancel={() => setRenaming(null)}
        onOk={handleRename}
        okText="保存"
        okButtonProps={{
          disabled: !renameValue.trim() || renameValue.trim() === renaming?.name,
          loading: savingRename,
        }}
        destroyOnHidden
      >
        <Input
          placeholder="新标签名"
          value={renameValue}
          maxLength={100}
          onChange={(e) => setRenameValue(e.target.value)}
          onPressEnter={handleRename}
          autoFocus
        />
      </Modal>
    </>
  )
}
