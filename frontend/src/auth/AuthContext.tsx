/**
 * 登录态(第三版):服务端 HttpOnly cookie 会话,前端只认 /auth/me 的结果。
 *
 * 结构:
 *   AuthProvider   挂载时查一次 /auth/me;登录 / 登出;注册 client.ts 的 401 回调
 *   useAuth()      读 { user, loading, login, logout, setUser }
 *   RequireUser    未登录 → /login(不含「改密门禁」,给 /change-password 用)
 *   RequireAuth    未登录 → /login;已登录但 must_change → /change-password(业务区用)
 *   RequireAdmin   非 admin → 回首页(套在账号管理路由外)
 */
import { createContext, useContext, useEffect, useMemo, useState } from 'react'
import { Navigate, Outlet, useLocation } from 'react-router-dom'
import { Spin } from 'antd'
import { fetchMe, login as apiLogin, logout as apiLogout } from '../api/resources'
import { setUnauthorizedHandler } from '../api/client'
import type { LoginPayload, User } from '../types'

interface AuthContextValue {
  /** null = 未登录 */
  user: User | null
  /** 挂载时首查 /auth/me 未结束(此时不要用门禁把用户弹去登录页) */
  loading: boolean
  login: (payload: LoginPayload) => Promise<User>
  logout: () => Promise<void>
  /** 改密成功等场景需要刷新本地用户(把 must_change_password 置否) */
  setUser: (user: User | null) => void
}

const AuthContext = createContext<AuthContextValue | null>(null)

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<User | null>(null)
  const [loading, setLoading] = useState(true)

  // 首查:刷新页面 / 新开标签时恢复登录态
  useEffect(() => {
    let active = true
    fetchMe()
      .then((u) => active && setUser(u))
      .catch(() => {
        /* 未登录或会话失效,保持 null */
      })
      .finally(() => active && setLoading(false))
    return () => {
      active = false
    }
  }, [])

  // 会话中途失效(过期 / 被管理员踢下线)→ 清本地用户让门禁去跳登录页
  useEffect(() => {
    setUnauthorizedHandler(() => {
      // 登录 / 设密页自己的 401 是表单报错,不触发全局登出跳转
      const path = window.location.pathname
      if (path === '/login' || path === '/set-password') return
      setUser(null)
    })
    return () => setUnauthorizedHandler(null)
  }, [])

  const value = useMemo<AuthContextValue>(
    () => ({
      user,
      loading,
      login: async (payload) => {
        const u = await apiLogin(payload)
        setUser(u)
        return u
      },
      logout: async () => {
        try {
          await apiLogout()
        } catch {
          /* 会话本就可能已失效,登出尽力而为 */
        }
        setUser(null)
      },
      setUser,
    }),
    [user, loading],
  )

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth 必须在 <AuthProvider> 内使用')
  return ctx
}

function PageLoading() {
  return (
    <div style={{ textAlign: 'center', padding: 80 }}>
      <Spin size="large" />
    </div>
  )
}

/** 仅要求「已登录」(不判改密门禁),给 /change-password 用。 */
export function RequireUser() {
  const { user, loading } = useAuth()
  if (loading) return <PageLoading />
  if (!user) return <Navigate to="/login" replace />
  return <Outlet />
}

/** 业务区门禁:未登录 → 登录页;改密前 → 改密页(否则业务接口会 403)。 */
export function RequireAuth() {
  const { user, loading } = useAuth()
  const location = useLocation()
  if (loading) return <PageLoading />
  if (!user)
    return <Navigate to="/login" replace state={{ from: location.pathname }} />
  if (user.must_change_password) return <Navigate to="/change-password" replace />
  return <Outlet />
}

/** 管理员专属路由。 */
export function RequireAdmin() {
  const { user } = useAuth()
  if (user?.role !== 'admin') return <Navigate to="/" replace />
  return <Outlet />
}
