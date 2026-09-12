import { useEffect, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import {
  App as AntApp,
  Button,
  Card,
  Divider,
  Form,
  Input,
  Select,
  Space,
  Spin,
} from 'antd'
import { PlusOutlined } from '@ant-design/icons'
import { createBlogTag, createPost, getPost, listBlogTags, updatePost } from '../api/resources'
import type { BlogStatus, BlogTag } from '../types'
import { BLOG_BODY_MAX_LEN } from '../types'

const { TextArea } = Input

interface FormValues {
  title: string
  body?: string
  tag_ids: number[]
}

/**
 * 新建(/blog/new)与编辑(/blog/:id/edit)共用的帖子表单页。
 *
 * **两个提交按钮,而不是一个「状态」下拉** —— 发布是个动作,不是字段。
 * 草稿能改回草稿,发布不能撤回(后端对已发布的帖子给 status="draft" 直接 422):
 * 所以帖子一旦发布,「存草稿」这个按钮就不再出现,只剩「保存修改」。
 * 按钮的有无就是那条规则的说明书。
 */
export default function BlogFormPage() {
  const { id } = useParams()
  const isEdit = id !== undefined
  const navigate = useNavigate()
  const { message } = AntApp.useApp()

  const [form] = Form.useForm<FormValues>()
  const [tags, setTags] = useState<BlogTag[]>([])
  const [newTagName, setNewTagName] = useState('')
  const [loading, setLoading] = useState(isEdit)
  const [saving, setSaving] = useState(false)
  /** 已保存的帖子当前是什么状态(null = 新建,还没有状态) */
  const [savedStatus, setSavedStatus] = useState<BlogStatus | null>(null)

  useEffect(() => {
    listBlogTags()
      .then(setTags)
      .catch((err) => message.error(err instanceof Error ? err.message : '加载标签失败'))
  }, [])

  // 编辑模式:预填现有数据
  useEffect(() => {
    if (!isEdit) return
    let cancelled = false
    setLoading(true)
    getPost(Number(id))
      .then((p) => {
        if (cancelled) return
        form.setFieldsValue({
          title: p.title,
          body: p.body ?? '',
          tag_ids: p.tags.map((t) => t.id),
        })
        setSavedStatus(p.status)
      })
      .catch((err) => {
        if (cancelled) return
        message.error(err instanceof Error ? err.message : '加载帖子失败')
        navigate('/blog', { replace: true })
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [isEdit, id])

  const handleCreateTagInline = async () => {
    const name = newTagName.trim()
    if (!name) return
    try {
      const created = await createBlogTag({ name })
      setTags((prev) => [...prev, created])
      // 把新标签并入当前已选
      const current = form.getFieldValue('tag_ids') ?? []
      form.setFieldsValue({ tag_ids: [...current, created.id] })
      setNewTagName('')
    } catch (err) {
      message.error(err instanceof Error ? err.message : '创建标签失败')
    }
  }

  /** status 为 undefined = 只保存不改变发布状态(已发布的帖子走这条)。 */
  const submit = async (status?: BlogStatus) => {
    let values: FormValues
    try {
      values = await form.validateFields()
    } catch {
      return // 校验失败,antd 已标红
    }

    setSaving(true)
    try {
      const payload = {
        title: values.title.trim(),
        body: values.body?.trim() || null,
        tag_ids: values.tag_ids ?? [],
        // 已发布的帖子**不能**带 status="draft"(后端 422:发布时间退不回去)
        ...(status !== undefined && savedStatus !== 'published' ? { status } : {}),
      }
      const saved = isEdit
        ? await updatePost(Number(id), payload)
        : await createPost(payload)

      message.success(
        saved.status === 'published'
          ? isEdit && savedStatus === 'published'
            ? '已保存修改'
            : `已发布「${saved.title}」`
          : '已存为草稿',
      )
      navigate(`/blog/${saved.id}`)
    } catch (err) {
      message.error(err instanceof Error ? err.message : '保存失败')
    } finally {
      setSaving(false)
    }
  }

  if (loading) {
    return (
      <div style={{ textAlign: 'center', padding: 80 }}>
        <Spin tip="加载中…" />
      </div>
    )
  }

  const isPublished = savedStatus === 'published'

  return (
    <Card title={isEdit ? '编辑帖子' : '写帖子'} style={{ maxWidth: 860 }}>
      <Form<FormValues>
        form={form}
        layout="vertical"
        onFinish={() => submit('draft')}
        initialValues={{ tag_ids: [] }}
      >
        <Form.Item
          name="title"
          label="标题"
          rules={[{ required: true, whitespace: true, message: '请填写标题' }]}
        >
          <Input placeholder="这篇写点什么" maxLength={255} />
        </Form.Item>

        <Form.Item name="tag_ids" label="标签">
          <Select
            mode="multiple"
            allowClear
            placeholder="选择标签,也可在下拉里直接新建"
            options={tags.map((t) => ({ value: t.id, label: t.name }))}
            dropdownRender={(menu) => (
              <>
                {menu}
                <Divider style={{ margin: '8px 0' }} />
                <Space style={{ padding: '0 8px 4px' }} wrap>
                  <Input
                    size="small"
                    placeholder="新标签名"
                    style={{ width: 180 }}
                    value={newTagName}
                    maxLength={100}
                    onChange={(e) => setNewTagName(e.target.value)}
                    onPressEnter={handleCreateTagInline}
                  />
                  <Button size="small" icon={<PlusOutlined />} onClick={handleCreateTagInline}>
                    新建
                  </Button>
                </Space>
              </>
            )}
          />
        </Form.Item>

        <Form.Item name="body" label="正文">
          <TextArea
            rows={18}
            maxLength={BLOG_BODY_MAX_LEN}
            showCount
            placeholder="正文(纯文本,换行会原样保留)"
            style={{ fontFamily: 'inherit' }}
          />
        </Form.Item>

        <Divider />

        <Space wrap>
          {/* 已发布的帖子没有「存草稿」这个选项:发布时间是一个已经发生的事实,
              退回去就得把它抹掉。真要撤回发布,那是删除的事。 */}
          {!isPublished && (
            <Button onClick={() => submit('draft')} loading={saving}>
              存草稿
            </Button>
          )}
          <Button type="primary" onClick={() => submit(isPublished ? undefined : 'published')} loading={saving}>
            {isPublished ? '保存修改' : isEdit ? '保存并发布' : '发布'}
          </Button>
          <Button onClick={() => navigate(isEdit ? `/blog/${id}` : '/blog')}>取消</Button>
        </Space>
      </Form>
    </Card>
  )
}
