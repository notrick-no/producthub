import { Link, Outlet, useLocation, useNavigate } from 'react-router-dom'
import { Avatar, Dropdown, Layout, Menu, Space, Typography } from 'antd'
import type { MenuProps } from 'antd'
import {
  AppstoreOutlined,
  HomeOutlined,
  KeyOutlined,
  LogoutOutlined,
  ProfileOutlined,
  ReadOutlined,
  RobotOutlined,
  SettingOutlined,
  TeamOutlined,
} from '@ant-design/icons'
import { useAuth } from '../auth/AuthContext'
import BrandLogo from './BrandLogo'

const { Sider, Header, Content } = Layout

/** 品牌强调色(取自 Logo 主色,与邀请邮件里的链接色一致) */
const BRAND = '#2464e4'

/**
 * 侧栏固定导航(第四版:侧栏不再罗列分类,分类筛选下放到产品分析页;
 * 第五版加博客;第六版加 AI)。
 *
 * ⚠️ **加一项要同时改这里和 `navKeyFor()`**。只改这个数组的话,新入口点得进去、
 * 菜单也会多出来,但那个页面不高亮 —— 因为高亮由 navKeyFor 的返回值决定,
 * 而它对未知路径返回 undefined。两个地方离得不远,就是放在一起的原因。
 */
const NAV_ITEMS: MenuProps['items'] = [
  { key: '/', icon: <HomeOutlined />, label: '首页' },
  { key: '/products', icon: <AppstoreOutlined />, label: '产品分析' },
  { key: '/requirements', icon: <ProfileOutlined />, label: '需求记录' },
  { key: '/blog', icon: <ReadOutlined />, label: '博客' },
  { key: '/ai', icon: <RobotOutlined />, label: 'AI 助手' },
]

/** 当前路径属于导航里的哪一项;不在导航里的页面(如账号管理)不高亮任何一项。 */
function navKeyFor(pathname: string): string | undefined {
  if (pathname === '/') return '/'
  if (pathname.startsWith('/products')) return '/products'
  if (pathname.startsWith('/requirements')) return '/requirements'
  if (pathname.startsWith('/blog')) return '/blog'
  // 这里**判等而不是 startsWith**:`/ai` 自己没有子路由,但 `/ai/settings`
  // (管理员)有 —— startsWith 会让打开设置页时侧栏的「AI 助手」亮着,
  // 而那两页不是一回事。将来真给对话页加了子路由再改成 startsWith。
  if (pathname === '/ai') return '/ai'
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
      ? [
          { key: 'accounts', icon: <TeamOutlined />, label: '账号管理' },
          // AI 设置挂在这里而不是侧栏 —— 侧栏是「所有人都用的功能」,
          // 而限额是管理员一个人的旋钮。同「账号管理」一个入口,不新增侧栏项。
          { key: 'ai-settings', icon: <SettingOutlined />, label: 'AI 设置' },
        ]
      : []),
    { key: 'password', icon: <KeyOutlined />, label: '修改密码' },
    { type: 'divider' as const },
    { key: 'logout', icon: <LogoutOutlined />, label: '退出登录', danger: true },
  ]

  const handleUserMenu: MenuProps['onClick'] = async ({ key }) => {
    if (key === 'accounts') {
      navigate('/users')
    } else if (key === 'ai-settings') {
      navigate('/ai/settings')
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
          // 这两个值同时被 index.css 的 :root 和撑满一屏的页面用着(见下面 Content 的注释)
          height: 'var(--app-header-h)',
          lineHeight: 'var(--app-header-h)',
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
        {/* ⚠️ 窄屏必须把它收起来(断点在下),否则 220px 的导航会把内容区
            挤成一条缝:390px 的机器上,220(导航)+ 24(内边距)之后只剩 146px。
            `collapsedWidth={0}` = 收起时宽度为 0,antd 会在左上角给一个「展开」的
            小把手(zero-width trigger),导航因此仍然点得到 —— 不是把导航藏起来。

            为什么是 md(768):手机 / 竖屏平板收起来,横屏平板(≥768)与桌面保持
            现在的样子。AI 对话页的上下堆叠断点是 880,两者故意不必相同 ——
            那是「页面内部怎么排」,这是「外壳留不留导航」。 */}
        <Sider
          theme="light"
          width={220}
          breakpoint="md"
          collapsedWidth={0}
          style={{ borderRight: '1px solid #f0f0f0' }}
        >
          <Menu
            mode="inline"
            items={NAV_ITEMS}
            selectedKeys={activeKey ? [activeKey] : []}
            onClick={({ key }) => navigate(key)}
            style={{ borderInlineEnd: 'none', paddingTop: 8 }}
          />
        </Sider>

        {/* 撑满一屏的页面(AI 对话页)要用 `calc(100dvh - 顶栏高 - 内容区上下内边距)`
            自己算高度,所以这三个尺寸抽成了 CSS 变量,和顶栏共用同一份 ——
            改这里的数字要改 index.css 的 :root,否则两边会悄悄错开。
            ⚠️ 别改成「给 Content 加 display:flex 让子元素 flex:1 撑满」:内容一旦高过
            视口,整条链上的高度都会退化成「由内容决定」,flex 和百分比就都拿不到确定高度,
            滚动区永远不会滚(实测过)。 */}
        <Content style={{ padding: 'var(--app-content-pad)', maxWidth: 1100 }}>
          <Outlet />
        </Content>
      </Layout>
    </Layout>
  )
}
