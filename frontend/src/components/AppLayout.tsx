import { useEffect, useState } from 'react'
import { Outlet, useNavigate, useSearchParams } from 'react-router-dom'
import {
  App as AntApp,
  Button,
  Input,
  Layout,
  Menu,
  Modal,
  Space,
  Typography,
} from 'antd'
import {
  AppstoreOutlined,
  FolderAddOutlined,
  FolderOutlined,
  PlusOutlined,
  SettingOutlined,
} from '@ant-design/icons'
import { createCategory, listCategories } from '../api/resources'
import type { Category } from '../types'
import CategoryManageModal from './CategoryManageModal'

const { Sider, Content } = Layout

/** 应用外壳:顶栏(搜索/新建)+ 左侧分类栏 + 内容区。 */
export default function AppLayout() {
  const navigate = useNavigate()
  const [params, setParams] = useSearchParams()
  const { message } = AntApp.useApp()

  const [categories, setCategories] = useState<Category[]>([])
  const [catOpen, setCatOpen] = useState(false)
  const [manageOpen, setManageOpen] = useState(false)
  const [newCatName, setNewCatName] = useState('')
  const [creating, setCreating] = useState(false)

  // 当前分类筛选:URL ?cat=<id|all>
  const activeCat = params.get('cat') ?? 'all'
  const q = params.get('q') ?? ''

  const loadCategories = async () => {
    try {
      const list = await listCategories()
      setCategories(list)
      // 若正在筛选的分类正好被删,自动回到「全部」
      const cat = new URLSearchParams(window.location.search).get('cat')
      if (cat && cat !== 'all' && !list.some((c) => String(c.id) === cat)) {
        updateParam('cat', null)
      }
    } catch {
      message.error('加载分类失败')
    }
  }

  useEffect(() => {
    loadCategories()
  }, [])

  const updateParam = (key: string, value: string | null) => {
    const next = new URLSearchParams(params)
    if (value) next.set(key, value)
    else next.delete(key)
    setParams(next)
  }

  const handleMenuSelect = ({ key }: { key: string }) => {
    updateParam('cat', key === 'all' ? null : key)
  }

  const handleCreateCategory = async () => {
    const name = newCatName.trim()
    if (!name) return
    setCreating(true)
    try {
      await createCategory({ name })
      message.success(`已创建分类「${name}」`)
      setNewCatName('')
      setCatOpen(false)
      await loadCategories()
    } catch (err) {
      message.error(err instanceof Error ? err.message : '创建失败')
    } finally {
      setCreating(false)
    }
  }

  const menuItems = [
    { key: 'all', icon: <AppstoreOutlined />, label: '全部' },
    { type: 'divider' as const },
    ...categories.map((c) => ({
      key: String(c.id),
      icon: <FolderOutlined />,
      label: c.name,
    })),
  ]

  return (
    <Layout style={{ minHeight: '100vh' }}>
      <Sider theme="light" width={220} style={{ borderRight: '1px solid #f0f0f0' }}>
        <div style={{ padding: '16px 20px 8px' }}>
          <Typography.Title level={4} style={{ margin: 0 }}>
            Producthut
          </Typography.Title>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            产品研究整理
          </Typography.Text>
        </div>

        <Menu
          mode="inline"
          items={menuItems}
          selectedKeys={[activeCat]}
          onClick={handleMenuSelect}
          style={{ borderInlineEnd: 'none' }}
        />

        <div
          style={{
            padding: 12,
            position: 'sticky',
            bottom: 0,
            background: '#fff',
            borderTop: '1px solid #f0f0f0',
          }}
        >
          <Space direction="vertical" style={{ width: '100%' }} size={4}>
            <Button
              block
              type="dashed"
              icon={<FolderAddOutlined />}
              onClick={() => setCatOpen(true)}
            >
              新建分类
            </Button>
            <Button block type="text" icon={<SettingOutlined />} onClick={() => setManageOpen(true)}>
              管理分类
            </Button>
          </Space>
        </div>
      </Sider>

      <Layout>
        <Content style={{ padding: 24, maxWidth: 1100 }}>
          {/* 顶栏动作:搜索 + 新建记录 */}
          <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 16 }}>
            <Input.Search
              allowClear
              placeholder="搜索产品名 / 网址"
              style={{ maxWidth: 320 }}
              defaultValue={q}
              onSearch={(v) => updateParam('q', v.trim() || null)}
            />
            <Button
              type="primary"
              icon={<PlusOutlined />}
              onClick={() => navigate('/products/new')}
            >
              新建记录
            </Button>
          </div>

          <Outlet context={{ categories, reloadCategories: loadCategories }} />
        </Content>
      </Layout>

      <Modal
        title="新建分类"
        open={catOpen}
        onOk={handleCreateCategory}
        onCancel={() => setCatOpen(false)}
        okText="创建"
        okButtonProps={{ disabled: !newCatName.trim(), loading: creating }}
        destroyOnHidden
      >
        <Input
          placeholder="分类名,如 AI、SaaS、效率工具"
          value={newCatName}
          onChange={(e) => setNewCatName(e.target.value)}
          onPressEnter={handleCreateCategory}
          maxLength={100}
          autoFocus
        />
      </Modal>

      <CategoryManageModal
        open={manageOpen}
        onClose={() => setManageOpen(false)}
        onChanged={loadCategories}
      />
    </Layout>
  )
}
