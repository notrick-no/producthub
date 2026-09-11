/**
 * 把库里存的网址变成一个能用的 href。
 *
 * 后端对 url 是宽松校验,明确「不强制带 scheme」(见 app/schemas.py 顶部注释),
 * 所以用户完全可能只填了 `example.com`。直接塞进 href 的话,浏览器会把它当成
 * 站内相对路径 —— 点开是本站的 404,而不是那个网站。没有 scheme 的一律补上 https://。
 *
 * 只放行带 `://` 的 scheme:这个字段是员工填的,但「填进去」和「渲染成可点的链接」
 * 是两回事,`javascript:` 这类不该从这里溜进 href(它没有 `//`,会被当域名补成 https://,
 * 于是失效而不是执行)。
 */
export function externalHref(url?: string | null): string | undefined {
  if (!url) return undefined
  const trimmed = url.trim()
  if (/^[a-z][a-z0-9+.-]*:\/\//i.test(trimmed)) return trimmed
  return `https://${trimmed}`
}
