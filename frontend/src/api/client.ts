/**
 * 轻量 API 客户端:所有请求打 /api,由 Vite 代理到后端。
 *
 * - 非 2xx 会抛出 Error,message 取自后端 FastAPI 的 {"detail": ...}
 * - 204(删除)返回 undefined
 */
const BASE = '/api'

type RequestInitLike = Omit<RequestInit, 'headers'> & {
  headers?: Record<string, string>
}

/**
 * 登录态失效(401)回调。由 AuthProvider 在挂载时注册:
 * 非登录页收到 401(会话过期 / 被踢下线)就清掉本地用户,门禁自动跳登录页。
 * 登录页本身(login/set-password)也常返回 401,那时只是表单报错,不触发跳转。
 */
let unauthorizedHandler: (() => void) | null = null
export function setUnauthorizedHandler(fn: (() => void) | null) {
  unauthorizedHandler = fn
}

export async function api<T>(path: string, init: RequestInitLike = {}): Promise<T> {
  const { headers, body, ...rest } = init
  // FormData(multipart 上传)由 fetch 自动带 boundary,不能手动设 Content-Type
  const contentType = body && !(body instanceof FormData) ? 'application/json' : undefined
  const res = await fetch(`${BASE}${path}`, {
    ...rest,
    headers: {
      ...(contentType ? { 'Content-Type': contentType } : {}),
      ...headers,
    },
    body,
  })

  if (!res.ok) {
    if (res.status === 401 && unauthorizedHandler) {
      unauthorizedHandler() // 通知登录态已失效(是否跳转由回调自己判断当前页面)
    }
    let detail = `请求失败(${res.status})`
    try {
      const data = await res.json()
      if (data?.detail) {
        detail = typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail)
      }
    } catch {
      /* 响应不是 JSON 时用默认文案 */
    }
    throw new Error(detail)
  }

  if (res.status === 204) {
    return undefined as T
  }
  return (await res.json()) as T
}

export function get<T>(path: string): Promise<T> {
  return api<T>(path)
}

export function post<T>(path: string, body: unknown): Promise<T> {
  return api<T>(path, { method: 'POST', body: JSON.stringify(body) })
}

/** multipart 上传(文件),Content-Type 交给 fetch 处理。 */
export function postForm<T>(path: string, form: FormData): Promise<T> {
  return api<T>(path, { method: 'POST', body: form })
}

export function patch<T>(path: string, body: unknown): Promise<T> {
  return api<T>(path, { method: 'PATCH', body: JSON.stringify(body) })
}

export function del(path: string): Promise<void> {
  return api<void>(path, { method: 'DELETE' })
}
