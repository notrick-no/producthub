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

export async function api<T>(path: string, init: RequestInitLike = {}): Promise<T> {
  const { headers, body, ...rest } = init
  const res = await fetch(`${BASE}${path}`, {
    ...rest,
    headers: {
      ...(body ? { 'Content-Type': 'application/json' } : {}),
      ...headers,
    },
    body,
  })

  if (!res.ok) {
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

export function patch<T>(path: string, body: unknown): Promise<T> {
  return api<T>(path, { method: 'PATCH', body: JSON.stringify(body) })
}

export function del(path: string): Promise<void> {
  return api<void>(path, { method: 'DELETE' })
}
