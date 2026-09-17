import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import rehypeHighlight from 'rehype-highlight'

/**
 * 渲染 AI 回答里的 Markdown。**只读展示,不接受任何交互**。
 *
 * ⚠️⚠️ **绝对不要加 `rehype-raw`。**
 *
 * 这里渲染的是**模型输出**,而模型的上下文里有用户写的产品简介、帖子正文、评论 ——
 * 也就是**任何人**都能往里面塞东西。加上 `rehype-raw` 就等于让那些内容里的
 * `<script>` / `<img onerror=…>` 变成真的 HTML 跑起来,一条现成的 XSS 路径。
 * 默认情况下 react-markdown **不渲染原始 HTML**,而是把它当纯文本显示 ——
 * 这正是我们要的行为,别去「修」它。
 *
 * ⚠️ 别把 `rehype-highlight` 和 `rehype-raw` 混为一谈,它们只共享一个前缀:
 *   · `rehype-highlight` —— 遍历**已经解析出来的**节点树,给代码块**加 className**
 *     (`hljs language-xxx`),交给 CSS 上色。它**不引入任何原始 HTML**。
 *   · `rehype-raw` —— 把 Markdown 里内嵌的 HTML 字符串**解析成真节点**再渲染。
 * 所以「已经有一个 rehype 插件了,再加一个应该没事」是错的。将来要加任何
 * rehype 插件,先确认它属于上面哪一类。
 *
 * 同理没上 `remark-math` / `rehype-katex`:那也是新依赖 + 新的注入面,现在没这个需求。
 */

interface MarkdownProps {
  children: string
  /** 流式生成中:末尾补一个光标,让「还在写」这件事一眼可见。 */
  streaming?: boolean
}

export default function Markdown({ children, streaming = false }: MarkdownProps) {
  return (
    <div className="markdown-body">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        // 常用语言由 rehype-highlight 自带的 lowlight `common` 集合覆盖(约 37 种),
        // 不额外注册 —— 引 highlight.js 全量(190+ 种)会把包撑大一倍多。
        rehypePlugins={[rehypeHighlight]}
      >
        {children}
      </ReactMarkdown>
      {streaming && <span className="markdown-caret" aria-hidden="true" />}
    </div>
  )
}
