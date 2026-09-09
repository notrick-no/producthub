import { useState } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { App as AntApp, Button, Card, Form, Input, Typography } from 'antd'
import { LockOutlined, MailOutlined } from '@ant-design/icons'
import { useAuth } from '../auth/AuthContext'

interface FormValues {
  email: string
  password: string
}

/** 登录页(第三版)。成功后按返回路径跳回,或去改密页。 */
export default function Login() {
  const { message } = AntApp.useApp()
  const navigate = useNavigate()
  const location = useLocation()
  const { login } = useAuth()
  const [submitting, setSubmitting] = useState(false)

  const from = (location.state as { from?: string } | null)?.from ?? '/'

  const handleFinish = async (values: FormValues) => {
    setSubmitting(true)
    try {
      const user = await login({
        email: values.email.trim(),
        password: values.password,
      })
      message.success(`欢迎回来,${user.name}`)
      // 重置过密码的用户:先强制改密,再进业务区
      navigate(user.must_change_password ? '/change-password' : from, { replace: true })
    } catch (err) {
      message.error(err instanceof Error ? err.message : '登录失败')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div
      style={{
        minHeight: '100vh',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        background: '#f5f5f5',
      }}
    >
      <Card style={{ width: 360 }} styles={{ body: { padding: '32px 28px' } }}>
        <div style={{ textAlign: 'center', marginBottom: 24 }}>
          <Typography.Title level={3} style={{ margin: 0 }}>
            producthub
          </Typography.Title>
          <Typography.Text type="secondary">产品研究整理 · 请登录</Typography.Text>
        </div>
        <Form<FormValues> layout="vertical" onFinish={handleFinish} requiredMark={false}>
          <Form.Item
            name="email"
            rules={[
              { required: true, message: '请输入邮箱' },
              { type: 'email', message: '请输入有效邮箱' },
            ]}
          >
            <Input
              prefix={<MailOutlined />}
              placeholder="登录邮箱"
              autoComplete="username"
              size="large"
            />
          </Form.Item>
          <Form.Item
            name="password"
            rules={[{ required: true, message: '请输入密码' }]}
          >
            <Input.Password
              prefix={<LockOutlined />}
              placeholder="密码"
              autoComplete="current-password"
              size="large"
            />
          </Form.Item>
          <Button
            type="primary"
            htmlType="submit"
            block
            size="large"
            loading={submitting}
            style={{ marginTop: 8 }}
          >
            登录
          </Button>
        </Form>
      </Card>
    </div>
  )
}
