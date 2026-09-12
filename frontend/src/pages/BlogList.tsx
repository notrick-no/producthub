import { useEffect, useMemo, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { App as AntApp, Button, Input, Select, Space, Table, Tag, Typography } from 'antd'
import type { TableColumnsType } from 'antd'
import { LikeOutlined, PlusOutlined, SettingOutlined } from '@ant-design/icons'
import { listBlogTags, listPosts } from '../api/resources'
import type { BlogPost, BlogTag } from '../types'
import { BLOG_STATUS_TEXT } from '../types'
import { BLOG_STATUS_COLOR } from '../blogMeta'
import { formatMonthDay } from '../format'
import { useUrlParams } from '../useUrlParams'
import BlogTagManageModal from '../components/BlogTagManageModal'
import ListToolbar from '../components/ListToolbar'

/**
 * 博客列表:表格 + 标签筛选(?tag=) + 状态筛选(?status=) + 搜索(?q=)。
 *
 * 列表的加载逻辑是从 ProductList / RequirementList 照抄来的 —— 第三处了,
 * 但三处的差别正好说明了为什么不抽:产品按 ?category_id= 走后端筛,需求全在前端,
 * 这里只有标签走后端(它是唯一一个「查不到就真少一条」的条件,后端 SQL 里有
 * `tag_links.any(...)`),另外两个筛选在前端做。抽成「通用列表页」得给三个回调,
 * 那才是抽象过度。
 *
 * **草稿**:员工只拿得到自己的草稿(后端滤过),管理员拿得到所有人的 ——
 * 所以「全部状态」这个选项对不同角色显示的条数不同,这是对的,不用在前端再滤一道。
 */
export default function BlogList() {
  const navigate = useNavigate()
  const { params, setParam } = useUrlParams()
  const { message } = AntApp.useApp()

  const tagParam = params.get('tag') ?? 'all'
  const tagId = tagParam !== 'all' ? Number(tagParam) : undefined
  const status = params.get('status') ?? 'all'
  const qRaw = params.get('q') ?? ''
  const q = qRaw.trim().toLowerCase()

  const [loading, setLoading] = useState(true)
  const [posts, setPosts] = useState<BlogPost[]>([])
  const [tags, setTags] = useState<BlogTag[]>([])
  const [manageOpen, setManageOpen] = useState(false)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    listPosts(tagId)
      .then((data) => {
        if (!cancelled) setPosts(data)
      })
      .catch((err) => {
        if (!cancelled) message.error(err instanceof Error ? err.message : '加载帖子失败')
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [tagId])

  /** 拉标签下拉的选项;若正在筛的标签已被删掉,自动退回「全部」。 */
  const loadTags = async () => {
    try {
      const list = await listBlogTags()
      setTags(list)
      if (tagId !== undefined && !list.some((t) => t.id === tagId)) {
        setParam('tag', null)
      }
    } catch {
      message.error('加载标签失败')
    }
  }

  // 进页面拉一次;之后标签的改名 / 删除由弹窗的回调触发重拉
  useEffect(() => {
    loadTags()
  }, [])

  const rows = useMemo(() => {
    let list = posts
    if (status !== 'all') list = list.filter((p) => p.status === status)
    if (q) {
      list = list.filter(
        (p) =>
          p.title.toLowerCase().includes(q) ||
          (p.body ?? '').toLowerCase().includes(q) ||
          p.author_name.toLowerCase().includes(q),
      )
    }
    return list
  }, [posts, status, q])

  const columns: TableColumnsType<BlogPost> = [
    {
      title: '标题',
      dataIndex: 'title',
      render: (_, p) => (
        <Link to={`/blog/${p.id}`} onClick={(e) => e.stopPropagation()}>
          <Typography.Text strong>{p.title}</Typography.Text>
        </Link>
      ),
    },
    {
      title: '标签',
      dataIndex: 'tags',
      width: 200,
      render: (_, p) => (
        <Space size={[4, 4]} wrap>
          {p.tags.map((t) => (
            <Tag key={t.id} color="blue">
              {t.name}
            </Tag>
          ))}
        </Space>
      ),
    },
    {
      title: '状态',
      dataIndex: 'status',
      width: 92,
      render: (_, p) => (
        <Tag color={BLOG_STATUS_COLOR[p.status]}>{BLOG_STATUS_TEXT[p.status]}</Tag>
      ),
    },
    {
      title: '作者',
      dataIndex: 'author_name',
      width: 120,
      ellipsis: true,
      render: (_, p) => p.author_name,
    },
    {
      title: '发布时间',
      dataIndex: 'published_at',
      width: 110,
      // 草稿没有发布时间,而这一列不叫「创建时间」—— 显示创建时间会让人以为
      // 它是按发布排的。写「未发布」比留一个 '—' 更说得出这条为什么排在那儿
      // (草稿按创建时间参与排序,见 routers/blog.py 的 coalesce)。
      sorter: (a, b) => (a.published_at ?? '').localeCompare(b.published_at ?? ''),
      render: (_, p) =>
        p.published_at ? (
          formatMonthDay(p.published_at)
        ) : (
          <Typography.Text type="secondary">未发布</Typography.Text>
        ),
    },
    {
      title: '点赞',
      dataIndex: 'like_count',
      width: 80,
      align: 'right',
      sorter: (a, b) => a.like_count - b.like_count,
      render: (_, p) =>
        p.like_count > 0 ? (
          <Typography.Text>
            <LikeOutlined /> {p.like_count}
          </Typography.Text>
        ) : (
          <Typography.Text type="secondary">—</Typography.Text>
        ),
    },
  ]

  return (
    <>
      <ListToolbar
        actions={
          <Button type="primary" icon={<PlusOutlined />} onClick={() => navigate('/blog/new')}>
            写帖子
          </Button>
        }
      >
        <Select
          value={tagParam}
          onChange={(v) => setParam('tag', v === 'all' ? null : v)}
          style={{ minWidth: 150 }}
          options={[
            { value: 'all', label: '全部标签' },
            ...tags.map((t) => ({ value: String(t.id), label: t.name })),
          ]}
        />
        <Select
          value={status}
          onChange={(v) => setParam('status', v === 'all' ? null : v)}
          style={{ minWidth: 130 }}
          options={[
            { value: 'all', label: '全部状态' },
            { value: 'published', label: '已发布' },
            { value: 'draft', label: '草稿' },
          ]}
        />
        <Input.Search
          allowClear
          placeholder="搜索标题 / 正文 / 作者"
          style={{ maxWidth: 260 }}
          defaultValue={qRaw}
          onSearch={(v) => setParam('q', v.trim() || null)}
        />
        <Button type="text" icon={<SettingOutlined />} onClick={() => setManageOpen(true)}>
          管理标签
        </Button>
      </ListToolbar>

      {/* 整行可点:点空白处也能进详情,不用非得瞄准标题。 */}
      <Table<BlogPost>
        rowKey="id"
        columns={columns}
        dataSource={rows}
        loading={loading}
        pagination={{ pageSize: 20, showTotal: (t) => `共 ${t} 篇` }}
        locale={{
          emptyText: q || status !== 'all' ? '没有匹配的帖子' : '还没有帖子,点「写帖子」开始',
        }}
        scroll={{ x: 840 }}
        onRow={(p) => ({
          style: { cursor: 'pointer' },
          onClick: () => {
            // 拖选一段文字时不该跳转 —— 否则选中想复制的内容,手一松页面就被带走了
            if (window.getSelection()?.toString()) return
            navigate(`/blog/${p.id}`)
          },
        })}
      />

      <BlogTagManageModal
        open={manageOpen}
        onClose={() => setManageOpen(false)}
        onChanged={loadTags}
      />
    </>
  )
}
