import { useState } from 'react'
import type { CSSProperties } from 'react'

/**
 * 品牌图形标志(山峰 + 波浪)。
 *
 * 资产由设计侧提供,放 public/brand/ 下;这里只负责摆放与降级:
 * 图片加载失败(资产还没放进来)时退回中性灰占位图形,保证布局可验证、不出现破图。
 * 占位图形只求中性,不代表品牌配色。
 */
interface BrandLogoProps {
  size?: number
  /** 深色底上使用浅色版资产(public/brand/logo-light.png) */
  variant?: 'default' | 'light'
  style?: CSSProperties
}

export default function BrandLogo({ size = 28, variant = 'default', style }: BrandLogoProps) {
  const [broken, setBroken] = useState(false)
  const box: CSSProperties = { display: 'block', flex: 'none', ...style }

  if (broken) {
    return (
      <svg
        width={size}
        height={size}
        viewBox="0 0 32 32"
        aria-hidden="true"
        focusable="false"
        style={box}
      >
        <path d="M19.4 4 29 22H9.8z" fill="#d9d9d9" />
        <path d="M13.2 7.5 22.4 22H4z" fill="#bfbfbf" />
        <path
          d="M2 24.5c3.2-2.1 6.4-2.1 9.6 0s6.4 2.1 9.6 0 6.4-2.1 9.6 0"
          fill="none"
          stroke="#bfbfbf"
          strokeWidth="2.4"
          strokeLinecap="round"
        />
        <path
          d="M2 28.5c3.2-2.1 6.4-2.1 9.6 0s6.4 2.1 9.6 0 6.4-2.1 9.6 0"
          fill="none"
          stroke="#d9d9d9"
          strokeWidth="1.7"
          strokeLinecap="round"
        />
      </svg>
    )
  }

  return (
    <img
      src={variant === 'light' ? '/brand/logo-light.png' : '/brand/logo.png'}
      width={size}
      height={size}
      alt=""
      aria-hidden="true"
      onError={() => setBroken(true)}
      style={{ ...box, objectFit: 'contain' }}
    />
  )
}
