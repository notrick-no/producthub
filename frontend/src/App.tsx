import { Suspense, lazy } from 'react'
import { Route, Routes } from 'react-router-dom'
import { Spin } from 'antd'
import AppLayout from './components/AppLayout'
import { AuthProvider, RequireAdmin, RequireAuth, RequireUser } from './auth/AuthContext'
import Login from './pages/Login'
import ChangePasswordPage from './pages/ChangePasswordPage'
import SetPasswordPage from './pages/SetPasswordPage'

// 路由级懒加载:每个页面独立 chunk,首屏只加载用到的
const HomePage = lazy(() => import('./pages/HomePage'))
const ProductList = lazy(() => import('./pages/ProductList'))
const ProductFormPage = lazy(() => import('./pages/ProductFormPage'))
const ProductDetail = lazy(() => import('./pages/ProductDetail'))
const RequirementList = lazy(() => import('./pages/RequirementList'))
const RequirementDetail = lazy(() => import('./pages/RequirementDetail'))
const RequirementFormPage = lazy(() => import('./pages/RequirementFormPage'))
const AccountsPage = lazy(() => import('./pages/AccountsPage'))

function PageFallback() {
  return (
    <div style={{ textAlign: 'center', padding: 80 }}>
      <Spin />
    </div>
  )
}

export default function App() {
  return (
    <AuthProvider>
      <Suspense fallback={<PageFallback />}>
        <Routes>
          {/* 公开:登录 / 邀请设密 */}
          <Route path="/login" element={<Login />} />
          <Route path="/set-password" element={<SetPasswordPage />} />

          {/* 已登录即可:修改密码(被重置密码的用户会被门禁强制先来这里) */}
          <Route element={<RequireUser />}>
            <Route path="/change-password" element={<ChangePasswordPage />} />
          </Route>

          {/* 业务区:登录 + 改密门禁 */}
          <Route element={<RequireAuth />}>
            <Route element={<AppLayout />}>
              <Route path="/" element={<HomePage />} />

              <Route path="/products" element={<ProductList />} />
              <Route path="/products/new" element={<ProductFormPage />} />
              <Route path="/products/:id" element={<ProductDetail />} />
              <Route path="/products/:id/edit" element={<ProductFormPage />} />

              <Route path="/requirements" element={<RequirementList />} />
              <Route path="/requirements/new" element={<RequirementFormPage />} />
              <Route path="/requirements/:id" element={<RequirementDetail />} />
              <Route path="/requirements/:id/edit" element={<RequirementFormPage />} />

              {/* 账号管理:仅管理员 */}
              <Route element={<RequireAdmin />}>
                <Route path="/users" element={<AccountsPage />} />
              </Route>
            </Route>
          </Route>
        </Routes>
      </Suspense>
    </AuthProvider>
  )
}
