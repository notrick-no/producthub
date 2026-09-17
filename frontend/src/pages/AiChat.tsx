import { useCallback, useEffect, useRef, useState } from 'react'
import {
  App as AntApp,
  Alert,
  Avatar,
  Button,
  Card,
  Empty,
  Flex,
  Space,
  Spin,
  Tag,
  Typography,
} from 'antd'
import { DeleteOutlined, PlusOutlined, RobotOutlined, UserOutlined } from '@ant-design/icons'
// ⚠️ **从子路径 import,不要 `from '@ant-design/x'`。** 两个原因,第二个是硬故障:
//
// 1. 根 barrel 会把 mermaid + react-syntax-highlighter 一并拉进模块图,
//    而这两个组件(Mermaid / CodeHighlighter)我们一个都不用,白白撑大包。
// 2. **barrel 在本项目里直接构建失败。** `code-highlighter` 依赖
//    `react-syntax-highlighter`,后者 ask 了 `highlight.js/lib/languages/sql_more`,
//    但它在自己 package.json 里钉的 `highlight.js@^10.4.1` 解析到 10.4.1 ——
//    那个版本**还没有** sql_more 这个文件(11 才加的)。于是报
//    「Rolldown failed to resolve import "highlight.js/lib/languages/sql_more"」。
//    这是它的上游打包 bug,与我们无关,但只要碰到那个模块就躲不开。
//    绕开 barrel 就完全碰不到它。
//
// 代价是引入了对 `es/` 目录结构的依赖。`@ant-design/x` 现在没有 `exports` 字段
// (所以子路径是允许的);将来它若加了 exports map,这几行要跟着改。
import Bubble from '@ant-design/x/es/bubble'
import Conversations from '@ant-design/x/es/conversations'
import Sender from '@ant-design/x/es/sender'
import ThoughtChain from '@ant-design/x/es/thought-chain'
import Welcome from '@ant-design/x/es/welcome'
import type { BubbleListProps } from '@ant-design/x/es/bubble'
import type { ConversationItemType } from '@ant-design/x/es/conversations'
import type { ThoughtChainItemType } from '@ant-design/x/es/thought-chain'
import {
  askAi,
  createAiConversation,
  deleteAiConversation,
  fetchAiStatus,
  getAiConversation,
  listAiConversations,
} from '../api/resources'
import type { AiConversation, AiConversationDetail, AiMessage, AiStatus, AiTurnUsage } from '../types'
import { AI_QUESTION_MAX_LEN, aiStatusText } from '../types'
import { relativeTime } from '../format'
import Markdown from '../components/Markdown'

/**
 * AI 助手页(第六版)。
 *
 * **只用 `@ant-design/x` 的组件,不用它的流式 SDK** —— `@ant-design/x-sdk` 的
 * `useXChat` 面向 OpenAI 兼容的 data 格式,而我们的 SSE 事件形状是自己定的
 * (见 app/ai_agent.py 文件头),套进去要写一层转接,还不如直接自己管状态。
 * 组件(Conversations / Bubble / Sender / ThoughtChain)是真省事,SDK 不是。
 *
 * ## 三条实现上的要点
 *
 * 1. **边收边 append,不攒完再渲染。** 循环 `for await` 事件,`content_delta`
 *    追加进 `streamText`。攒完再渲染的话,流式就只剩「等得久但最后一次性出现」。
 * 2. **流结束后重拉一次会话详情**,用库里那一份替换掉内存里累积的 —— 权威数据在库里
 *    (含 status / error / token 用量 / 被截断的 reasoning),而且答案是流中断时
 *    唯一能确知「到底存下了什么」的方式。两边正文在正常情况下逐字相同,所以不会闪。
 * 3. **停止生成 = abort**。abort 让服务端拿到 GeneratorExit,把那半截按 `interrupted`
 *    落库 —— 钱是用户在出的,不能因为关了页面就当没发生。
 */

/** 流进行中的一个工具调用。`ok === null` = 已发出、结果还没回来。 */
interface StreamingToolCall {
  name: string
  args: Record<string, unknown>
  ok: boolean | null
  preview: string
}

/** Bubble.List 的角色配置:决定气泡朝向、形状、头像。
 * **显式传 `role`**,不赌组件内置的默认值 —— 默认值不在它的类型里,升级时可能变。 */
const BUBBLE_ROLES: BubbleListProps['role'] = {
  user: {
    placement: 'end',
    variant: 'filled',
    avatar: <Avatar size={28} icon={<UserOutlined />} style={{ background: '#2464e4' }} />,
  },
  ai: {
    placement: 'start',
    variant: 'outlined',
    avatar: <Avatar size={28} icon={<RobotOutlined />} style={{ background: '#f0f0f0', color: '#2464e4' }} />,
  },
}

/** 对话列表里的一个会话 → 组件要的形状(`key` 必须是字符串,组件按字符串比对 activeKey)。 */
function toConversationItem(c: AiConversation): ConversationItemType {
  return { key: String(c.id), label: c.title, group: groupOf(c.updated_at) }
}

/**
 * 库里的 `role`(assistant / user)→ `Bubble.List` 的 `role` 键(ai / user)。
 *
 * ⚠️ **这个映射不能省。** 组件是拿 `items[].role` 去 `role` 配置里查的
 * (`role[item.role]`,`BubbleList.js`),而 `BUBBLE_ROLES` 的键只能是
 * `ai | system | user`(类型上就不接受 `assistant`)。直接把库里的 `assistant`
 * 填进去**不会报错** —— `BUBBLE_ROLES['assistant']` 是 `undefined`,
 * 于是那一整条消息的气泡配置被整个丢掉(头像没了、variant 回到默认),
 * 而 `placement` 恰好默认就是 `'start'`,看起来「好像是对的」。
 */
function bubbleRole(role: AiMessage['role']): 'ai' | 'user' {
  return role === 'assistant' ? 'ai' : 'user'
}

/** 按「今天 / 昨天 / 更早」分组。一天一变,所以每次渲染重算没有意义 —— 这里就按当前时刻算。 */
function groupOf(iso: string): string {
  const then = new Date(iso)
  const now = new Date()
  const days = Math.floor(
    (new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime() -
      new Date(then.getFullYear(), then.getMonth(), then.getDate()).getTime()) /
      86400000,
  )
  if (days <= 0) return '今天'
  if (days === 1) return '昨天'
  return '更早'
}

/** 工具名 → 中文说明。工具本身的中文名在后端(app/ai_tools.py 的 ToolSpec.description),
 * 那是给模型看的;这里是给**人**看的,两句话不一样,所以不复用。 */
const TOOL_LABELS: Record<string, string> = {
  search_products: '搜索产品',
  get_product: '查看产品详情',
  search_requirements: '搜索需求',
  get_requirement: '查看需求详情',
  search_posts: '搜索帖子',
  get_post: '查看帖子详情',
  list_categories: '列出分类',
  list_blog_tags: '列出帖子标签',
  get_summary: '统计站内内容',
  recent_activity: '查看最近动态',
}

function toolLabel(name: string): string {
  return TOOL_LABELS[name] ?? name
}

/** 一份工具调用 → ThoughtChain 的一项。 */
function toolItem(call: StreamingToolCall, key: string): ThoughtChainItemType {
  const args = Object.entries(call.args)
    .map(([k, v]) => `${k}=${typeof v === 'string' ? v : JSON.stringify(v)}`)
    .join('  ')
  return {
    key,
    title: toolLabel(call.name),
    description: args || undefined,
    status: call.ok === null ? 'loading' : call.ok ? 'success' : 'error',
    collapsible: true,
    content: call.preview ? (
      // 工具结果是**模型上下文**,后端只推截断过的预览(见 app/ai_agent.py)。
      // 这里也当纯文本渲染 —— 它的内容来自用户写的产品简介 / 帖子正文。
      <div className="ai-thought-text">{call.preview}</div>
    ) : undefined,
  }
}

export default function AiChat() {
  const { message, modal } = AntApp.useApp()

  const [status, setStatus] = useState<AiStatus | null>(null)
  const [bootLoading, setBootLoading] = useState(true)
  const [conversations, setConversations] = useState<AiConversation[]>([])
  const [activeId, setActiveId] = useState<number | null>(null)
  const [messages, setMessages] = useState<AiMessage[]>([])
  const [detailLoading, setDetailLoading] = useState(false)

  // 流进行中的状态。全都在一次回答结束后清空,由库里的那份接管。
  const [streaming, setStreaming] = useState(false)
  // **这一股流属于哪个会话。** 不记下来的话,「用户问完 A 就切到 B」会让 A 的
  // 半截答案画在 B 的界面里(见下面 liveHere 的用法)。
  const [streamConvId, setStreamConvId] = useState<number | null>(null)
  const [streamText, setStreamText] = useState('')
  const [streamReasoning, setStreamReasoning] = useState('')
  const [streamTools, setStreamTools] = useState<StreamingToolCall[]>([])
  const [streamUsage, setStreamUsage] = useState<AiTurnUsage | null>(null)

  const [input, setInput] = useState('')
  const abortRef = useRef<AbortController | null>(null)
  // 回答收尾时要知道「用户此刻还看着这个会话吗」,而 send 的回调闭包里那个
  // activeId 是提问那一刻的旧值。ref 是这里唯一能读到最新值的办法。
  const activeIdRef = useRef<number | null>(null)

  // ---- 滚动 ----
  // 消息区**只有一个滚动容器**(下面那层 div,样式在 index.css 的 .ai-chat-scroll)。
  // 思考过程和气泡都在它里面一起滚 —— 这也正是它必须是「一个确定高度的盒子」的原因:
  // 高度由内容决定的话就没有滚动可言,内容只会一路长到卡片外面去。
  const scrollRef = useRef<HTMLDivElement | null>(null)
  // 「用户此刻贴着底吗」。往上翻看历史时**不能**把他拽回底部 —— 那是聊天界面最恼人的
  // 行为之一;但只要他自己滚回底部附近,后面的流式内容就重新跟手。
  const stickToBottom = useRef(true)

  const handleScroll = useCallback(() => {
    const el = scrollRef.current
    if (!el) return
    // 32px 容差:流式内容每来一块 scrollHeight 都会跳,不留余量会误判成「用户翻走了」
    stickToBottom.current = el.scrollHeight - el.scrollTop - el.clientHeight < 32
  }, [])

  const refreshStatus = useCallback(async () => {
    try {
      setStatus(await fetchAiStatus())
    } catch (err) {
      message.error(err instanceof Error ? err.message : '读取 AI 状态失败')
    }
  }, [message])

  // ---- 首屏:状态 + 会话列表,并默认打开最近的一个 ----
  useEffect(() => {
    let cancelled = false
    setBootLoading(true)
    Promise.all([fetchAiStatus(), listAiConversations()])
      .then(([s, list]) => {
        if (cancelled) return
        setStatus(s)
        setConversations(list)
        if (list.length > 0) setActiveId(list[0].id)
      })
      .catch((err) => {
        if (!cancelled) message.error(err instanceof Error ? err.message : '加载 AI 会话失败')
      })
      .finally(() => {
        if (!cancelled) setBootLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [message])

  // ---- 切换会话:拉详情 ----
  useEffect(() => {
    activeIdRef.current = activeId
    // 切走 = 这一轮到此为止。不这么做的话,流会继续往「上一个会话」里写,
    // 而界面已经在看另一个会话了 —— 半截答案要么串台,要么在收尾时把新会话盖掉。
    // 代价说清楚:服务端收到断连会把已生成的部分存下来、状态标成「已中断」,
    // 所以切回去看得到它停在哪(不是凭空消失)。钱也是这时候就不花了。
    //
    // ⚠️ 判据必须是「**流属于别的会话**」,不能是「有流在跑」:新会话的第一个问题
    // 会先 `setActiveId(新 id)` 再开流,那也会触发这个 effect。无条件 abort 会把
    // 用户刚敲下去的问题当场掐死 —— 而且只在「新会话」这条路上出现,很难查。
    if (streamConvId != null && streamConvId !== activeId) {
      abortRef.current?.abort()
    }
    // 换会话 = 从头读,重新贴到底(哪怕上一个会话里用户正翻着历史)
    stickToBottom.current = true
    // 上一轮的 token 数是**那个会话**的账,切过来就不该再显示了
    setStreamUsage(null)
    if (activeId == null) {
      setMessages([])
      return
    }
    let cancelled = false
    setDetailLoading(true)
    getAiConversation(activeId)
      .then((detail) => {
        // ⚠️ 这一股流正跑在这个会话上时**不能**铺详情:库里此刻还没有那轮问答
        // (提问行和答案都是流跑完才落的),铺上来会把用户刚敲下去的问题抹掉,
        // 而且是一抹一整轮 —— 流式期间屏幕上只剩下一颗转圈的加载。
        // 只可能发生在「新会话的第一个问题」:那条路先 setActiveId 再开流,会把这个 effect 叫醒。
        if (!cancelled && streamConvId !== activeId) setMessages(detail.messages)
      })
      .catch((err) => {
        if (!cancelled) message.error(err instanceof Error ? err.message : '加载会话失败')
      })
      .finally(() => {
        if (!cancelled) setDetailLoading(false)
      })
    return () => {
      cancelled = true
    }
    // `streamConvId` **故意不在依赖里**(lint 会提这一条,是知情的):
    // 它是「activeId 变的那一刻,手上这股流属于谁」,这正是我们要问的问题;
    // 进了依赖就会在开流时把这个 effect 又叫醒一次,白白重拉一遍详情。
  }, [activeId, message])

  // ---- 卸载时停掉正在跑的流(组件没了,没人消费了) ----
  useEffect(() => () => abortRef.current?.abort(), [])

  // ---- 内容长了一截就贴到底 ----
  // 依赖里带上每一次流式增量 —— 「逐字跟手」就是从这里来的,而不是靠 Bubble.List 的
  // autoScroll(它滚的是自己那个盒子,而滚动权已经交给外面这层了)。
  useEffect(() => {
    const el = scrollRef.current
    if (el && stickToBottom.current) el.scrollTop = el.scrollHeight
  }, [activeId, messages, streamText, streamReasoning, streamTools, detailLoading])

  /**
   * 拉一次会话详情。**只取数据,不改状态** —— 什么时候把库里那份换上去,
   * 由调用方在一次 setState 批次里决定(见 send 的 finally:连流式那份一起收,
   * 否则会出现「库里那份进来了、流式那份还没收」的中间态,答案闪一下变两份)。
   * 失败返回 null,由调用方决定怎么办(而不是静默什么都不做)。
   */
  const fetchDetail = useCallback(async (id: number) => {
    try {
      return await getAiConversation(id)
    } catch {
      return null
    }
  }, [])

  /** 把一份详情铺到页面上。`intoMessages=false` 时**只更新侧栏那一行** ——
   * 用户已经把界面切到别的会话了,这时去改 `messages` 等于拿 A 的消息盖住 B 的界面。 */
  const applyDetail = useCallback((detail: AiConversationDetail, intoMessages = true) => {
    if (intoMessages) setMessages(detail.messages)
    setConversations((prev) =>
      prev.map((c) =>
        c.id === detail.id
          ? {
              ...c,
              title: detail.title,
              message_count: detail.message_count,
              updated_at: detail.updated_at,
            }
          : c,
      ),
    )
  }, [])

  /** 拉起流式那一份的累积状态。`streamConvId` 一并清掉 —— 见那里的说明。 */
  const clearStream = useCallback(() => {
    setStreaming(false)
    setStreamConvId(null)
    setStreamText('')
    setStreamReasoning('')
    setStreamTools([])
  }, [])

  const send = useCallback(
    async (raw: string) => {
      const content = raw.trim()
      if (!content || streaming) return
      setInput('')

      // **会话是懒创建的**:进来就看一眼不问的人不该在库里留下一行空会话。
      let id = activeId
      try {
        if (id == null) {
          const created = await createAiConversation()
          setConversations((prev) => [created, ...prev])
          setActiveId(created.id)
          id = created.id
        }
      } catch (err) {
        message.error(err instanceof Error ? err.message : '新建会话失败')
        return
      }

      // 乐观追加提问行:等到流结束再重拉的话,用户敲完回车会先愣一下
      setMessages((prev) => [
        ...prev,
        {
          id: -Date.now(), // 负数 id = 本地临时行,重拉时被库里的真实行替换
          role: 'user',
          content,
          status: 'done',
          created_at: new Date().toISOString(),
        },
      ])
      setStreaming(true)
      setStreamConvId(id)
      setStreamText('')
      setStreamReasoning('')
      setStreamTools([])
      setStreamUsage(null)

      const controller = new AbortController()
      abortRef.current = controller
      // 事件是**边到边拼**的,用局部变量累积:setState 是异步的,
      // 在一个 chunk 里连读两次 state 会读到同一份旧值。
      let text = ''
      let reasoning = ''
      const tools: StreamingToolCall[] = []

      try {
        for await (const event of askAi(id, content, controller.signal)) {
          switch (event.type) {
            case 'start':
              // 首问之后后端会把标题改成问题本身,侧栏要跟着变
              if (event.title) {
                setConversations((prev) =>
                  prev.map((c) => (c.id === id ? { ...c, title: event.title as string } : c)),
                )
              }
              break
            case 'reasoning_delta':
              reasoning += event.text
              setStreamReasoning(reasoning)
              break
            case 'content_delta':
              text += event.text
              setStreamText(text)
              break
            case 'tool':
              tools.push({ name: event.name, args: event.args, ok: null, preview: '' })
              setStreamTools([...tools])
              break
            case 'tool_result': {
              // 从后往前找**最近一个同名且还没结果**的调用。同名工具可能连着调两次,
              // 从前往后找会把第二次的结果记到第一次头上。
              for (let i = tools.length - 1; i >= 0; i -= 1) {
                if (tools[i].name === event.name && tools[i].ok === null) {
                  tools[i] = { ...tools[i], ok: event.ok, preview: event.preview }
                  break
                }
              }
              setStreamTools([...tools])
              break
            }
            case 'done':
              setStreamUsage(event.usage)
              break
            case 'error':
              // 服务端已经把这次回答标成 failed 了,重拉之后正文里会带上错误说明。
              // 这里只提示一句,不自己往正文里塞字 —— 那样两边就不一致了。
              message.error(event.detail)
              break
          }
        }
      } catch (err) {
        // abort 是「用户点了停止」,不是错误。其余才是真的失败。
        if (!(err instanceof DOMException && err.name === 'AbortError')) {
          message.error(err instanceof Error ? err.message : '提问失败')
        }
      } finally {
        abortRef.current = null
        // **先拿库里的权威版本,再一次渲染里同时换上去。**
        // 顺序反了会闪:先收起流式那份、再等重拉,答案会短暂消失;
        // 反过来先铺库里那份、再收流式那份,答案会短暂变成两份。
        // 两个 setState 在同一个 await 之后连着调用,React 会批处理成一次渲染。
        //
        // `stillOpen`:用户可能已经切到别的会话了,那就**只更新侧栏那行,别碰界面**
        // (切走时流已经被 abort,这里跑的是它的收尾)。
        const stillOpen = activeIdRef.current === id
        const detail = await fetchDetail(id)
        if (detail) {
          applyDetail(detail, stillOpen)
          clearStream()
        } else if (stillOpen) {
          // 拉不到就**留着流式那份**,别让用户眼看着答案凭空消失。
          setStreaming(false)
          message.error('回答已生成,但刷新会话失败。切换一下会话可以看到完整内容。')
        } else {
          // 切走了、这次重拉也不成:侧栏那行更新不了,但界面保持干净。
          clearStream()
        }
        await refreshStatus()
      }
    },
    [activeId, streaming, message, fetchDetail, applyDetail, clearStream, refreshStatus],
  )

  const handleDelete = useCallback(
    (id: number) => {
      modal.confirm({
        title: '删除这个会话?',
        content: '会话里的全部问答会一起删掉,不能恢复。',
        okText: '删除',
        okType: 'danger',
        cancelText: '取消',
        onOk: async () => {
          try {
            await deleteAiConversation(id)
            setConversations((prev) => prev.filter((c) => c.id !== id))
            if (activeId === id) {
              // 删的正好是打开着的那个:切到列表里的下一个(没有就回到空态)
              const rest = conversations.filter((c) => c.id !== id)
              setActiveId(rest.length > 0 ? rest[0].id : null)
            }
          } catch (err) {
            message.error(err instanceof Error ? err.message : '删除失败')
          }
        },
      })
    },
    [activeId, conversations, message, modal],
  )

  const newConversation = useCallback(() => {
    // 只是回到空态,**不建行** —— 真正建行发生在第一次提问时(与懒创建一致),
    // 这样连点五次「新会话」不会在库里留下五行空会话。
    setActiveId(null)
    setMessages([])
    setInput('')
  }, [])

  // ---- 输入框为什么是灰的:三种原因的话术和该找的人都不一样 ----
  const blocked: string | null = !status
    ? null
    : !status.configured
      ? 'AI 未配置:请联系部署的人在服务端设置 DEEPSEEK_API_KEY。'
      : !status.enabled
        ? 'AI 助手已被管理员关闭。'
        : status.month_budget_exceeded
          ? `本月额度已用完(已用 ${status.month_tokens_used} tokens),请联系管理员调整。`
          : status.remaining_today <= 0
            ? `今日提问次数已用完(${status.asked_today}/${status.daily_questions_per_user}),每天 0 点(北京时间)重置。`
            : null

  const conversationItems = conversations.map(toConversationItem)

  // 这一轮流式内容**该不该画在这块界面上**。两个条件缺一不可:
  //   ① 屏幕上还有东西(或者流正在跑,要显示「加载中」)—— 流结束、库里那份铺上来
  //      之前有一小段两种状态并存的窗口,判据宽一点才不会闪;
  //   ② **它属于当前打开的这个会话**。用户问答到一半切走时,abort 到收尾之间还有
  //      一小段时间,不加这一条,A 的半截答案会画进 B 的界面里。
  const liveHere =
    streamConvId != null &&
    streamConvId === activeId &&
    (streaming || streamText !== '' || streamReasoning !== '' || streamTools.length > 0)

  // 历史消息 + (流进行中的那一轮)。流结束后重拉,这一段就换成库里的版本。
  const bubbleItems: BubbleListProps['items'] = [
    ...messages.map((m) => ({
      key: String(m.id),
      role: bubbleRole(m.role),
      content: m.content,
      header:
        m.role === 'assistant' ? (
          <Space size={6} wrap>
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              {relativeTime(m.created_at)}
            </Typography.Text>
            {m.status !== 'done' && (
              <Tag color={m.status === 'running' ? 'processing' : 'warning'} style={{ marginInlineEnd: 0 }}>
                {aiStatusText(m.status)}
              </Tag>
            )}
            {m.error && (
              <Typography.Text type="danger" style={{ fontSize: 12 }}>
                {m.error}
              </Typography.Text>
            )}
          </Space>
        ) : undefined,
      // 助手正文走 Markdown;用户提问按纯文本渲染 —— 用户自己敲的 `#` 不该变成标题。
      // (react-markdown 不渲染原始 HTML,见 components/Markdown.tsx 的 ⚠️)
      contentRender:
        m.role === 'assistant'
          ? (content: string) => <Markdown>{content}</Markdown>
          : undefined,
      footer:
        m.role === 'assistant' && (m.prompt_tokens != null || m.completion_tokens != null) ? (
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            {`${(m.prompt_tokens ?? 0) + (m.completion_tokens ?? 0)} tokens`}
          </Typography.Text>
        ) : undefined,
    })),
    ...(liveHere
      ? [
          {
            key: 'streaming',
            role: 'ai' as const,
            content: streamText,
            loading: !streamText && !streamReasoning && streamTools.length === 0,
            contentRender: (content: string) => <Markdown streaming>{content}</Markdown>,
          },
        ]
      : []),
  ]

  // 思考过程 + 工具轨迹,合成一条 ThoughtChain(在回答上方,可折叠)。
  // 同样按 liveHere 收口:切走的会话的思考过程不该留在别人的界面上。
  const thinkingItems: ThoughtChainItemType[] = !liveHere
    ? []
    : [
        ...(streamReasoning
          ? [
              {
                key: 'reasoning',
                title: '思考过程',
                status: 'success' as const,
                collapsible: true,
                content: <div className="ai-thought-text">{streamReasoning}</div>,
              },
            ]
          : []),
        ...streamTools.map((call, index) => toolItem(call, `tool-${index}`)),
      ]

  if (bootLoading) {
    return (
      <div style={{ textAlign: 'center', padding: 80 }}>
        <Spin />
      </div>
    )
  }

  return (
    // 整页撑满一屏(高度算法与那两行 ⚠️ 都在 index.css 的 .ai-page 里):
    // 对话面板因此是「一屏」,而不是一个拍脑袋定高的小盒子。
    <Flex vertical gap={16} className="ai-page">
      <Flex justify="space-between" align="center" wrap gap={8}>
        <Typography.Title level={4} style={{ margin: 0 }}>
          AI 助手
        </Typography.Title>
        {status && (
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            {status.configured
              ? `模型 ${status.model} · 今日还可问 ${status.remaining_today} 次`
              : '未配置'}
          </Typography.Text>
        )}
      </Flex>

      {blocked && <Alert type="warning" showIcon message={blocked} />}

      {/* 卡片自己是一个纵向 flex,body 撑满卡片、两列再撑满 body。
          不用 Space/Card 的默认高度是因为它们的每一项都按内容高,撑不出「一屏」。 */}
      <Card
        styles={{
          root: {
            flex: 1,
            minHeight: 0,
            display: 'flex',
            flexDirection: 'column',
            overflow: 'hidden',
          },
          body: { flex: 1, minHeight: 0, padding: 0, display: 'flex' },
        }}
      >
        <Flex align="stretch" style={{ flex: 1, minWidth: 0, minHeight: 0 }}>
          {/* 左列:会话列表。它自己滚 —— 会话攒多了不该把右边的面板一起顶长。 */}
          <div
            style={{
              width: 240,
              flex: 'none',
              borderRight: '1px solid #f0f0f0',
              padding: 8,
              overflowY: 'auto',
            }}
          >
            <Button
              block
              icon={<PlusOutlined />}
              onClick={newConversation}
              style={{ marginBottom: 8 }}
            >
              新会话
            </Button>
            {conversationItems.length === 0 ? (
              <Empty
                image={Empty.PRESENTED_IMAGE_SIMPLE}
                description="还没有会话"
                style={{ marginTop: 24 }}
              />
            ) : (
              <Conversations
                items={conversationItems}
                activeKey={activeId != null ? String(activeId) : undefined}
                onActiveChange={(key) => setActiveId(Number(key))}
                groupable
                menu={(item) => ({
                  items: [{ key: 'delete', icon: <DeleteOutlined />, label: '删除', danger: true }],
                  onClick: ({ key, domEvent }) => {
                    domEvent.stopPropagation() // 别让点击穿透到「切换到该会话」
                    if (key === 'delete') handleDelete(Number(item.key))
                  },
                })}
              />
            )}
          </div>

          {/* 右列:消息流(唯一滚动容器)+ 输入框 */}
          <Flex vertical style={{ flex: 1, minWidth: 0, minHeight: 0 }}>
            <div
              ref={scrollRef}
              onScroll={handleScroll}
              className="ai-chat-scroll"
              style={{ flex: 1, minHeight: 0, padding: 16 }}
            >
              {messages.length === 0 && !liveHere ? (
                <Welcome
                  variant="borderless"
                  icon={<RobotOutlined style={{ color: '#2464e4' }} />}
                  title="问点什么"
                  description="我会去站内已发布的产品、需求、帖子里找答案。草稿不参与检索。"
                />
              ) : (
                <Spin spinning={detailLoading}>
                  {/* 思考过程与气泡在**同一个滚动容器**里,一起滚。
                      从前它单独待在外面且高度不限,模型一旦想得长一点,整块就顶出卡片。 */}
                  {thinkingItems.length > 0 && (
                    <div className="ai-thinking" style={{ marginBottom: 12 }}>
                      <ThoughtChain items={thinkingItems} defaultExpandedKeys={['reasoning']} />
                    </div>
                  )}
                  {/* ⚠️ maxHeight 必须是 none:组件默认 max-height:100%,在「父级有确定
                      高度」时会把它自己也限高,于是长会话在面板内部又多出一条滚动条
                      (两条滚动条各滚各的),而且上面那块思考过程不跟着走。
                      滚动权归外面那层 div。autoScroll 同理去掉 —— 它滚的是组件自己
                      那个盒子,现在由 handleScroll 接管。 */}
                  <Bubble.List items={bubbleItems} role={BUBBLE_ROLES} style={{ maxHeight: 'none' }} />
                </Spin>
              )}
            </div>

            <div style={{ flex: 'none', borderTop: '1px solid #f0f0f0', padding: 12 }}>
              <Sender
                value={input}
                onChange={setInput}
                onSubmit={(text) => send(text)}
                onCancel={() => abortRef.current?.abort()}
                loading={streaming}
                disabled={blocked != null}
                placeholder={blocked ?? '问点什么…(Enter 发送,Shift+Enter 换行)'}
                autoSize={{ minRows: 1, maxRows: 6 }}
              />
              <Flex justify="space-between" align="center" style={{ marginTop: 6 }}>
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                  {input.length > AI_QUESTION_MAX_LEN
                    ? `已超出 ${input.length - AI_QUESTION_MAX_LEN} 字`
                    : `AI 只读已发布内容,看不到任何草稿${
                        streamUsage ? ` · 上一轮 ${streamUsage.total_tokens} tokens / ${streamUsage.elapsed_ms} ms` : ''
                      }`}
                </Typography.Text>
              </Flex>
            </div>
          </Flex>
        </Flex>
      </Card>
    </Flex>
  )
}
