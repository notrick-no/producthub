import { useState } from 'react'

/**
 * 潮汐抽象画(登录页左栏背景)。
 *
 * 每层排三份:原图、水平镜像、原图,整体平移两份的长度即走完一轮。
 * 用镜像而不是直接复制,是因为素材左右边缘接不上(实测首尾列像素差是相邻列的
 * 6~15 倍,即不可横向平铺),直接拼接会在接缝处"错一下";镜像图的边缘天然等于原图
 * 的边缘,所以接缝两侧像素完全一致。
 *
 * 三份是最少够用的数量:窗口要滑过两份的长度(a → a' → a),结尾停在第三份上,
 * 与开头那份画面完全相同,所以循环点无跳变。多一份就多一屏的显存。
 * 图层高度也只留波浪那一条(见 index.css 的 aspect-ratio),别让透明区占显存。
 *
 * 三层速度不同即产生视差(速度见 index.css 的 .tide-layer--*)。
 * 资产缺失时退回中性灰占位波浪,动效一致。
 *
 * 调参:--tide-scale 整体放大缩小,--tide-offset 单层上下微调(见 index.css)。
 */
const LAYERS = [
  { src: '/brand/tide-back.png', className: 'tide-layer tide-layer--back' },
  { src: '/brand/tide-mid.png', className: 'tide-layer tide-layer--mid' },
  { src: '/brand/tide-front.png', className: 'tide-layer tide-layer--front' },
] as const

/** 占位波浪的三层灰(由远及近渐深),仅用于资产缺失时 */
const PLACEHOLDER_FILLS = ['#ececec', '#dedede', '#cfcfcf']

/** 占位波浪(单个周期 960 宽,4 × 240),四份拼接同样走镜像 */
const PLACEHOLDER_WAVE =
  'M0 60 c60 -30 180 -30 240 0 s180 30 240 0 s180 -30 240 0 s180 30 240 0 L960 200 L0 200 Z'

/** 每层三份:序号为奇数的用 CSS 水平镜像(见 index.css 的 nth-child(even)) */
const COPIES = [0, 1, 2] as const

export default function TideArt() {
  const [broken, setBroken] = useState(false)

  return (
    <div className="tide-art" aria-hidden="true">
      {LAYERS.map((layer, i) => (
        <div key={layer.src} className={layer.className}>
          {broken
            ? COPIES.map((k) => (
                <svg key={k} viewBox="0 0 960 200" preserveAspectRatio="none" focusable="false">
                  <path d={PLACEHOLDER_WAVE} fill={PLACEHOLDER_FILLS[i]} />
                </svg>
              ))
            : COPIES.map((k) => (
                <img key={k} src={layer.src} alt="" onError={() => setBroken(true)} />
              ))}
        </div>
      ))}
    </div>
  )
}
