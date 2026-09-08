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
import { deleteCategory, listCategories, listProducts, updateCategory } from '../api/resources'
import type { Category } from '../types'

interface Props {
  open: boolean
  onClose: () => void
  /** 改名 / 删除成功后通知父级,让它刷新侧栏的分类列表 */
  onChanged: () => void
}

/**
 * 管理分类弹窗:列出全部分类,支持改名 / 删除。
 * 每行显示该分类被多少个产品使用(打开时拉一次全量产品统计)。
 */
export default function CategoryManageModal({ open, onClose, onChanged }: Props) {
  const { message } = AntApp.useApp()

  const [cats, setCats] = useState<Category[]>([])
  const [loading, setLoading] = useState(false)
  // 分类 id → 使用它的产品数
  const [counts, setCounts] = useState<Map<number, number>>(new Map())

  // 正在重命名的分类(null = 未在改名)
  const [renaming, setRenaming] = useState<Category | null>(null)
  const [renameValue, setRenameValue] = useState('')
  const [savingRename, setSavingRename] = useState(false)

  // 每次打开都拉一份新鲜数据(分类 + 产品,用于统计使用数)
  useEffect(() => {
    if (!open) return
    let cancelled = false
    setLoading(true)
    Promise.all([listCategories(), listProducts()])
      .then(([categoryList, products]) => {
        if (cancelled) return
        setCats(categoryList)
        const m = new Map<number, number>()
        for (const p of products) {
          for (const c of p.categories) m.set(c.id, (m.get(c.id) ?? 0) + 1)
        }
        setCounts(m)
      })
      .catch((err) => {
        if (!cancelled) message.error(err instanceof Error ? err.message : '加载分类失败')
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [open])

  const startRename = (c: Category) => {
    setRenaming(c)
    setRenameValue(c.name)
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
      await updateCategory(renaming.id, { name })
      message.success(`已改名为「${name}」`)
      // 本地同步 + 通知父级刷新侧栏
      setCats((prev) => prev.map((c) => (c.id === renaming.id ? { ...c, name } : c)))
      onChanged()
      setRenaming(null)
    } catch (err) {
      message.error(err instanceof Error ? err.message : '重命名失败')
    } finally {
      setSavingRename(false)
    }
  }

  const handleDelete = async (c: Category) => {
    try {
      await deleteCategory(c.id)
      message.success(`已删除「${c.name}」`)
      setCats((prev) => prev.filter((x) => x.id !== c.id))
      onChanged()
    } catch (err) {
      message.error(err instanceof Error ? err.message : '删除失败')
    }
  }

  return (
    <>
      <Modal
        title="管理分类"
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
        ) : cats.length === 0 ? (
          <Empty description="还没有分类。可在左侧「新建分类」,或在新建记录时顺手创建" />
        ) : (
          <>
            <Typography.Paragraph type="secondary" style={{ marginBottom: 8 }}>
              改名或删除分类。删除后产品记录保留,仅移除其上的分类标签。
            </Typography.Paragraph>
            <List
              dataSource={cats}
              rowKey="id"
              renderItem={(c) => {
                const count = counts.get(c.id) ?? 0
                return (
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
                      <Space size={10} wrap>
                        <Typography.Text strong>{c.name}</Typography.Text>
                        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                          {count > 0 ? `${count} 个产品` : '暂无产品'}
                        </Typography.Text>
                      </Space>
                      <Space size={4}>
                        <Tooltip title="重命名">
                          <Button
                            type="text"
                            icon={<EditOutlined />}
                            onClick={() => startRename(c)}
                          />
                        </Tooltip>
                        <Popconfirm
                          title={`删除分类「${c.name}」?`}
                          description={
                            count > 0
                              ? `该分类已用于 ${count} 个产品,删除后这些产品将变为未分类(产品记录保留)。`
                              : '该分类暂无产品使用。'
                          }
                          okText="删除"
                          okButtonProps={{ danger: true }}
                          onConfirm={() => handleDelete(c)}
                        >
                          <Tooltip title="删除">
                            <Button type="text" danger icon={<DeleteOutlined />} />
                          </Tooltip>
                        </Popconfirm>
                      </Space>
                    </div>
                  </List.Item>
                )
              }}
            />
          </>
        )}
      </Modal>

      {/* 重命名小窗:复用新建分类的输入样式 */}
      <Modal
        title="重命名分类"
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
          placeholder="新分类名"
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
