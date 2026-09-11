import { Link, Outlet, useLocation, useNavigate } from 'react-router-dom'
import { Avatar, Dropdown, Layout, Menu, Space, Typography } from 'antd'
import type { MenuProps } from 'antd'
import {
  AppstoreOutlined,
  HomeOutlined,
  KeyOutlined,
  LogoutOutlined,
  ProfileOutlined,
  TeamOutlined,
} from '@ant-design/icons'
import { useAuth } from '../auth/AuthContext'
import BrandLogo from './BrandLogo'

const { Sider, Header, Content } = Layout

/** 品牌强调色(取自 Logo 主色,与邀请邮件里的链接色一致) */
const BRAND = '#2464e4'

/** 侧栏三项固定导航(第四版:侧栏不再罗列分类,分类筛选下放到产品分析页)。 */
const NAV_ITEMS: MenuProps['items'] = [
  { key: '/', icon: <HomeOutlined />, label: '首页' },
  { key: '/products', icon: <AppstoreOutlined />, label: '产品分析' },
  { key: '/requirements', icon: <ProfileOutlined />, label: '需求记录' },
]

/** 当前路径属于导航里的哪一项;不在导航里的页面(如账号管理)不高亮任何一项。 */
function navKeyFor(pathname: string): string | undefined {
  if (pathname === '/') return '/'
  if (pathname.startsWith('/products')) return '/products'
  if (pathname.startsWith('/requirements')) return '/requirements'
  return undefined
}

/**
 * 应用外壳:顶栏(Logo + 用户菜单,**横贯整宽**)+ 左侧导航 + 内容区。
 *
 * 第四版把顶栏提到最外层 —— 它属于整个应用,不该被左侧导航截住。
 * 搜索框和「新建」按钮不在这里:它们是页面级的动作,各自待在列表页的工具条里
 * (顶栏只剩品牌与用户,也就不再需要按路径判断"这页该不该显示搜索")。
 */
export default function AppLayout() {
  const navigate = useNavigate()
  const location = useLocation()
  const { user, logout } = useAuth()

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

  const activeKey = navKeyFor(location.pathname)

  return (
    <Layout style={{ minHeight: '100vh' }}>
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
        <Link
          to="/"
          aria-label="返回首页"
          style={{ display: 'flex', alignItems: 'center', gap: 10, flex: 'none' }}
        >
          <BrandLogo size={26} />
          <Typography.Title level={4} style={{ margin: 0 }}>
            producthub
          </Typography.Title>
        </Link>

        <Dropdown
          menu={{ items: userMenuItems, onClick: handleUserMenu }}
          placement="bottomRight"
        >
          <Space style={{ cursor: 'pointer' }}>
            <Avatar size="small" style={{ background: BRAND, verticalAlign: 'middle' }}>
              {user?.name?.charAt(0) ?? '?'}
            </Avatar>
            <Typography.Text>{user?.name}</Typography.Text>
          </Space>
        </Dropdown>
      </Header>

      {/* 内层 Layout 带 Sider,antd 自动判定为横向;它 flex:auto,撑满顶栏以下的高度 */}
      <Layout>
        <Sider theme="light" width={220} style={{ borderRight: '1px solid #f0f0f0' }}>
          <Menu
            mode="inline"
            items={NAV_ITEMS}
            selectedKeys={activeKey ? [activeKey] : []}
            onClick={({ key }) => navigate(key)}
            style={{ borderInlineEnd: 'none', paddingTop: 8 }}
          />
        </Sider>

        <Content style={{ padding: 24, maxWidth: 1100 }}>
          <Outlet />
        </Content>
      </Layout>
    </Layout>
  )
}
