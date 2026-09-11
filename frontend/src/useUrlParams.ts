import { useCallback } from 'react'
import { useSearchParams } from 'react-router-dom'

/**
 * URL 查询参数就是筛选状态的唯一出处(?q= / ?cat=)。
 *
 * 写 null 或空串等于**删掉**这个键 —— 于是「清空筛选」和「从没筛选过」在地址栏里
 * 长得一样,刷新、分享链接、浏览器后退都能还原出同一份列表。
 *
 * 这六行原本在产品分析页和需求记录页各抄了一份,逐字相同。
 */
export function useUrlParams() {
  const [params, setParams] = useSearchParams()

  const setParam = useCallback(
    (key: string, value: string | null) => {
      const next = new URLSearchParams(params)
      if (value) next.set(key, value)
      else next.delete(key)
      setParams(next)
    },
    [params, setParams],
  )

  return { params, setParam }
}
