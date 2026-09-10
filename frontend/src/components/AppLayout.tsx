import { useEffect, useState } from 'react'
import { Link, Outlet, useLocation, useNavigate, useSearchParams } from 'react-router-dom'
import {
  App as AntApp,
  Avatar,
  Button,
  Dropdown,
  Input,
  Layout,
  Menu,
  Modal,
  Space,
  Typography,
} from 'antd'
import type { MenuProps } from 'antd'
import {
  AppstoreOutlined,
  FolderAddOutlined,
  FolderOutlined,
  KeyOutlined,
  LogoutOutlined,
  PlusOutlined,
  SettingOutlined,
  TeamOutlined,
} from '@ant-design/icons'
import { createCategory, listCategories } from '../api/resources'
import type { Category } from '../types'
import { useAuth } from '../auth/AuthContext'
import BrandLogo from './BrandLogo'
import CategoryManageModal from './CategoryManageModal'

const { Sider, Header, Content } = Layout

/** 品牌强调色(取自 Logo 主色,与邀请邮件里的链接色一致) */
const BRAND = '#2464e4'

/** 应用外壳:左侧分类栏 + 顶栏(搜索/新建 + 头像菜单)+ 内容区。 */
export default function AppLayout() {
  const navigate = useNavigate()
  const location = useLocation()
  const [params, setParams] = useSearchParams()
  const { message } = AntApp.useApp()
  const { user, logout } = useAuth()

  const [categories, setCategories] = useState<Category[]>([])
  const [catOpen, setCatOpen] = useState(false)
  const [manageOpen, setManageOpen] = useState(false)
  const [newCatName, setNewCatName] = useState('')
  const [creating, setCreating] = useState(false)

  // 账号管理页没有「搜索 / 新建记录」这类产品动作
  const isAccounts = location.pathname === '/users'

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
    if (!isAccounts) loadCategories()
  }, [isAccounts])

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

  const userMenuItems: MenuProps['items'] = [
    ...(user?.role === 'admin'
      ? [{ key: 'accounts', icon: <TeamOutlined />, label: '账号管理' }]
      : []),
    { key: 'password', icon: <KeyOutlined />, label: '修改密码' },
    { type: 'divider' as const },
    { key: 'logout', icon: <LogoutOutlined />, label: '退出登录', danger: true },
  ]

  const handleUserMenu: MenuProps['onClick'] = async ({ key }) => {
    if (key === 'accounts') {
      navigate('/users')
    } else if (key === 'password') {
      navigate('/change-password')
    } else if (key === 'logout') {
      await logout()
      navigate('/login', { replace: true })
    }
  }

  return (
    <Layout style={{ minHeight: '100vh' }}>
      <Sider theme="light" width={220} style={{ borderRight: '1px solid #f0f0f0' }}>
        <div style={{ padding: '16px 20px 8px' }}>
          <Typography.Title level={4} style={{ margin: 0 }}>
            producthub
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
        <Header
          style={{
            background: '#fff',
            borderBottom: '1px solid #f0f0f0',
            padding: '0 24px',
            height: 56,
            lineHeight: '56px',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            gap: 12,
          }}
        >
          {/* 顶部导航左侧:Logo(右侧是头像菜单) */}
          <div style={{ flex: 1, minWidth: 0, display: 'flex', alignItems: 'center', gap: 12 }}>
            <Link to="/" aria-label="返回首页" style={{ display: 'flex', flex: 'none' }}>
              <BrandLogo size={26} />
            </Link>
            {!isAccounts && (
              <Input.Search
                allowClear
                placeholder="搜索产品名 / 网址"
                style={{ maxWidth: 320 }}
                defaultValue={q}
                onSearch={(v) => updateParam('q', v.trim() || null)}
              />
            )}
          </div>
          <Space size={12}>
            {!isAccounts && (
              <Button
                type="primary"
                icon={<PlusOutlined />}
                onClick={() => navigate('/products/new')}
              >
                新建记录
              </Button>
            )}
            <Dropdown
              menu={{ items: userMenuItems, onClick: handleUserMenu }}
              placement="bottomRight"
            >
              <Space style={{ cursor: 'pointer' }}>
                <Avatar
                  size="small"
                  style={{ background: BRAND, verticalAlign: 'middle' }}
                >
                  {user?.name?.charAt(0) ?? '?'}
                </Avatar>
                <Typography.Text>{user?.name}</Typography.Text>
              </Space>
            </Dropdown>
          </Space>
        </Header>

        <Content style={{ padding: 24, maxWidth: 1100 }}>
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
