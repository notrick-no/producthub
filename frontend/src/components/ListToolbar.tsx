import type { ReactNode } from 'react'

interface ListToolbarProps {
  /** 左侧:分类下拉、搜索框这类筛选控件,按传入顺序排。 */
  children?: ReactNode
  /** 右侧:主操作(新建…),自动贴到最右。 */
  actions?: ReactNode
}

/**
 * 列表页顶部的工具条。
 *
 * 抽的是那八行逐字相同的容器样式 —— 容器在哪个列表页都得是这个长相,
 * 属于「稳定重复」。里面放什么、右侧有什么按钮,仍然各页自己写。
 */
export default function ListToolbar({ children, actions }: ListToolbarProps) {
  return (
    <div
      style={{
        display: 'flex',
        flexWrap: 'wrap',
        alignItems: 'center',
        gap: 12,
        marginBottom: 16,
      }}
    >
      {children}
      {/* 撑开剩余宽度,把 actions 顶到最右;没传 actions 时它不占位,左侧照旧靠左 */}
      <div style={{ flex: 1 }} />
      {actions}
    </div>
  )
}
