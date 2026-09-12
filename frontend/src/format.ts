/**
 * 时间显示。几个函数产出几种精度,各有各的用处 —— 别顺手合并成「一个能传参的」。
 *
 * 合并之前它们散在四个页面里,其中两个页面还重名(`formatTime` 在两处是不同格式),
 * 看名字以为是同一个东西,改一处另一处不动。
 */

/** 列表里的「更新时间」:9/11。当年的记录不必带年份。 */
export function formatMonthDay(iso: string): string {
  const d = new Date(iso)
  return d.toLocaleDateString('zh-CN', { month: 'numeric', day: 'numeric' })
}

/** 详情页的时间戳:2026年9月11日 10:05。详情页要看得出是哪一年哪一刻。 */
export function formatDate(iso: string): string {
  return new Date(iso).toLocaleString('zh-CN', { dateStyle: 'medium', timeStyle: 'short' })
}

/** 账号表的日期:2026/09/11。账号是按天看的,年份省不掉。 */
export function formatFullDate(iso: string): string {
  const d = new Date(iso)
  return d.toLocaleDateString('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  })
}

/**
 * 刚刚 / N 分钟前 / N 小时前 / N 天前,超过一周显示日期。
 *
 * 动态流和评论都在用(第二处 = 稳定重复,所以从 HomePage 提上来了)。
 */
export function relativeTime(iso: string): string {
  const minutes = Math.floor((Date.now() - new Date(iso).getTime()) / 60000)
  if (minutes < 1) return '刚刚'
  if (minutes < 60) return `${minutes} 分钟前`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours} 小时前`
  const days = Math.floor(hours / 24)
  if (days < 7) return `${days} 天前`
  return new Date(iso).toLocaleDateString('zh-CN', { month: 'numeric', day: 'numeric' })
}
