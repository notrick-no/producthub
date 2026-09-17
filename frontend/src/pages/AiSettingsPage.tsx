import { useCallback, useEffect, useState } from 'react'
import {
  App as AntApp,
  Button,
  Card,
  Flex,
  Form,
  InputNumber,
  Space,
  Statistic,
  Switch,
  Table,
  Typography,
} from 'antd'
import type { TableColumnsType } from 'antd'
import { fetchAiSettings, fetchAiUsage, updateAiSettings } from '../api/resources'
import type { AiSettingsPayload, AiUsage, AiUsageUser } from '../types'
import { AI_BUDGET_MAX, AI_DAILY_MAX, AI_DAILY_MIN, AI_MAX_TOKENS_MAX, AI_MAX_TOKENS_MIN } from '../types'
import { formatDate } from '../format'

/**
 * AI 限额设置(管理员)。第六版。
 *
 * **为什么是一个页面而不是环境变量**:限额是运营旋钮,管理员不是部署者 ——
 * 让它去改 .env 再等重启,等于这个旋钮实际上不存在。
 *
 * 页面上是**两组东西,别混着看**:
 *   · 上面是**旋钮**(设成多少),PUT 全量提交;
 *   · 下面是**读数**(这个月花了多少、今天各人问了几次),只读。
 * 读数里**只有数字,没有提问内容** —— 管理员管的是额度,不是别人问了什么
 * (见 app/permissions.py 里 can_manage_ai_settings 与 can_view_ai_conversation
 * 的分界,以及 app/routers/ai.py 的 get_ai_usage 注释)。
 *
 * `monthly_token_budget` 为空 = **不限**,是一个明确的选择,所以下面显示「不限」
 * 而不是「—」或 0。这也是这个页面唯一一处「空值有意义」的地方。
 */
export default function AiSettingsPage() {
  const { message } = AntApp.useApp()
  const [form] = Form.useForm<AiSettingsPayload>()

  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [savedAt, setSavedAt] = useState<string | null>(null)
  const [usage, setUsage] = useState<AiUsage | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const [settings, report] = await Promise.all([fetchAiSettings(), fetchAiUsage()])
      form.setFieldsValue({
        enabled: settings.enabled,
        // 注意别写成 `?? 0`:null 是「不限」,写成 0 会变成「一点都不许用」
        monthly_token_budget: settings.monthly_token_budget,
        daily_questions_per_user: settings.daily_questions_per_user,
        max_tokens_per_call: settings.max_tokens_per_call,
      })
      setSavedAt(settings.updated_at)
      setUsage(report)
    } catch (err) {
      message.error(err instanceof Error ? err.message : '加载 AI 设置失败')
    } finally {
      setLoading(false)
    }
  }, [form, message])

  useEffect(() => {
    void load()
  }, [load])

  const onSave = async () => {
    let values: AiSettingsPayload
    try {
      values = await form.validateFields()
    } catch {
      return // 校验失败:表单自己会标红,不用再弹一句
    }
    setSaving(true)
    try {
      const settings = await updateAiSettings(values)
      setSavedAt(settings.updated_at)
      message.success('已保存')
      // 读数跟着刷新:月度预算改了,「还剩多少」的判断也就变了
      setUsage(await fetchAiUsage())
    } catch (err) {
      message.error(err instanceof Error ? err.message : '保存失败')
    } finally {
      setSaving(false)
    }
  }

  const columns: TableColumnsType<AiUsageUser> = [
    { title: '用户', dataIndex: 'name', key: 'name' },
    {
      title: '今日提问',
      dataIndex: 'questions_today',
      key: 'questions_today',
      align: 'right',
      sorter: (a, b) => a.questions_today - b.questions_today,
    },
    {
      title: '本月 tokens',
      dataIndex: 'tokens_this_month',
      key: 'tokens_this_month',
      align: 'right',
      defaultSortOrder: 'descend',
      sorter: (a, b) => a.tokens_this_month - b.tokens_this_month,
    },
  ]

  const budget = usage?.monthly_token_budget ?? null
  const used = usage?.month_tokens_used ?? 0

  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      <Typography.Title level={4} style={{ margin: 0 }}>
        AI 设置
      </Typography.Title>

      <Card
        title="限额"
        loading={loading}
        extra={
          <Button type="primary" loading={saving} onClick={onSave}>
            保存
          </Button>
        }
      >
        <Form form={form} layout="vertical" style={{ maxWidth: 520 }}>
          <Form.Item
            name="enabled"
            label="启用 AI 助手"
            valuePropName="checked"
            extra="关掉之后所有人都问不了,输入框会提示「已被管理员关闭」。"
          >
            <Switch />
          </Form.Item>

          <Form.Item
            name="daily_questions_per_user"
            label="每人每日提问次数"
            rules={[{ required: true, message: '请填一个次数' }]}
            extra="按**北京时间**的 0 点重置(不是 UTC —— 按 UTC 算的话用户会在早上 8 点莫名其妙被重置)。"
          >
            <InputNumber min={AI_DAILY_MIN} max={AI_DAILY_MAX} style={{ width: 160 }} />
          </Form.Item>

          <Form.Item
            name="max_tokens_per_call"
            label="单次回答的 token 上限"
            rules={[{ required: true, message: '请填一个上限' }]}
            extra="指一次回答最多生成多少 token。调大能让长回答不被截断,但也会让单次花费变高。"
          >
            <InputNumber
              min={AI_MAX_TOKENS_MIN}
              max={AI_MAX_TOKENS_MAX}
              step={256}
              style={{ width: 160 }}
            />
          </Form.Item>

          <Form.Item
            name="monthly_token_budget"
            label="每月 token 预算"
            extra="**留空 = 不限**。填了之后,全站本月累计用量达到这个数就会拒绝新的提问(对所有人),直到下个月 1 号。"
          >
            <InputNumber
              min={0}
              max={AI_BUDGET_MAX}
              step={100000}
              style={{ width: 200 }}
              placeholder="不限"
            />
          </Form.Item>
        </Form>

        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          {savedAt ? `上次修改:${formatDate(savedAt)}` : '还没有人改过,当前显示的是默认值'}
        </Typography.Text>
      </Card>

      <Card title="本月用量" loading={loading}>
        <Flex gap={48} wrap style={{ marginBottom: 16 }}>
          <Statistic title="本月已用 tokens" value={used} />
          <Statistic title="本月预算" value={budget == null ? '不限' : budget} />
          {budget != null && (
            <Statistic
              title="剩余"
              value={Math.max(0, budget - used)}
              valueStyle={{ color: used >= budget ? '#cf1322' : undefined }}
            />
          )}
        </Flex>

        <Table<AiUsageUser>
          rowKey={(row) => String(row.user_id ?? row.name)}
          columns={columns}
          dataSource={usage?.users ?? []}
          pagination={false}
          size="small"
          locale={{ emptyText: '本月还没有人用过' }}
        />
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          只统计用量,看不到任何提问内容 —— 会话是每个人的私事,管理员也打不开。
        </Typography.Text>
      </Card>
    </Space>
  )
}
