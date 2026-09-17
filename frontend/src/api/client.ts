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

/** 非 2xx 时从响应里取出给用户看的文案(FastAPI 的 {"detail": ...})。 */
async function errorFrom(res: Response): Promise<Error> {
  let detail = `请求失败(${res.status})`
  try {
    const data = await res.json()
    if (data?.detail) {
      detail = typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail)
    }
  } catch {
    /* 响应不是 JSON 时用默认文案 */
  }
  return new Error(detail)
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
    throw await errorFrom(res)
  }

  if (res.status === 204) {
    return undefined as T
  }
  return (await res.json()) as T
}

/**
 * 流式 POST(SSE 的读取端)。异步生成器:每收到一个事件就 yield 一个解析好的对象。
 *
 * **为什么不用 `EventSource`**:它只支持 GET,而提问要带请求体。手写
 * `fetch` + `res.body.getReader()` 反而更短,也顺带能中止(见下面的 cancel)。
 *
 * **为什么错误是普通 `Error` 而不是一个 error 事件**:后端把「未配置 / 被管理员关闭 /
 * 超出配额」这些判断**全放在开流之前**,那时返回的是普通 JSON(见 app/routers/ai.py
 * 的注释),所以这里 `!res.ok` 分支的处理和 `api()` 里那段是同一套 —— 调用方
 * try/catch 一个 Error 就够了。
 *
 * 中断:`opts.signal` 或消费方提前 `break`(两者都会走到 finally 的 cancel)。
 * 服务端据此拿到 GeneratorExit,把那半截回答按 `interrupted` 落库 ——
 * 钱是用户在出的,不能因为关了页面就当没发生(见 app/ai_agent.py 的 except GeneratorExit)。
 */
export async function* postStream<T>(
  path: string,
  body: unknown,
  opts: { signal?: AbortSignal } = {},
): AsyncGenerator<T> {
  const res = await fetch(`${BASE}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
    signal: opts.signal,
  })

  if (!res.ok) {
    if (res.status === 401 && unauthorizedHandler) {
      unauthorizedHandler()
    }
    throw await errorFrom(res)
  }
  if (!res.body) {
    // 理论上现代浏览器都有;给一句能看懂的,而不是让下面的 getReader 抛 TypeError
    throw new Error('当前浏览器不支持流式响应')
  }

  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  try {
    for (;;) {
      const { done, value } = await reader.read()
      if (done) break
      // stream: true —— 一个多字节字符可能正好跨在两块之间,不能按块解码
      buffer += decoder.decode(value, { stream: true })

      // SSE 用空行分隔事件。**最后一段可能不完整**,留在 buffer 里等下一块 ——
      // 按块直接 JSON.parse 会在长回答上偶发失败(而且只在慢网络下复现)。
      let sep = buffer.indexOf('\n\n')
      while (sep !== -1) {
        const frame = buffer.slice(0, sep)
        buffer = buffer.slice(sep + 2)
        sep = buffer.indexOf('\n\n')

        const line = frame.split('\n').find((l) => l.startsWith('data:'))
        if (!line) continue // 注释行 / 心跳,跳过
        const payload = line.slice(5).trim()
        if (payload) yield JSON.parse(payload) as T
      }
    }
  } finally {
    // 正常读完时 cancel 是无害的;提前退出时它才是关键 —— 不取消的话
    // 服务端会一直生成到结束,而那份钱是要算的。
    reader.cancel().catch(() => {})
  }
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

/** PUT:全量替换。目前只有 AI 设置用它(四个旋钮一起提交,是 PUT 而不是 PATCH)。 */
export function put<T>(path: string, body: unknown): Promise<T> {
  return api<T>(path, { method: 'PUT', body: JSON.stringify(body) })
}

export function del(path: string): Promise<void> {
  return api<void>(path, { method: 'DELETE' })
}
