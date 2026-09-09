import { useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { Alert, App as AntApp, Button, Card, Form, Input, Typography } from 'antd'
import { LockOutlined } from '@ant-design/icons'
import { useAuth } from '../auth/AuthContext'
import { setPassword } from '../api/resources'

interface FormValues {
  new_password: string
  confirm: string
}

/**
 * 邀请链接设初始密码:管理员建号后收到邮件,点链接进这里。
 * 设置成功即自动登录,直接回首页(链接一次性,刷新后 token 不可复用)。
 */
export default function SetPasswordPage() {
  const { message } = AntApp.useApp()
  const navigate = useNavigate()
  const { setUser } = useAuth()
  const [params] = useSearchParams()
  const [submitting, setSubmitting] = useState(false)
  const token = params.get('token') ?? ''

  const handleFinish = async (values: FormValues) => {
    setSubmitting(true)
    try {
      const user = await setPassword(token, values.new_password)
      setUser(user)
      message.success('密码设置成功,已自动登录')
      navigate('/', { replace: true })
    } catch (err) {
      message.error(err instanceof Error ? err.message : '设置失败')
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
      <Card style={{ width: 380 }} styles={{ body: { padding: '32px 28px' } }}>
        <div style={{ textAlign: 'center', marginBottom: 24 }}>
          <Typography.Title level={4} style={{ margin: 0 }}>
            设置登录密码
          </Typography.Title>
          <Typography.Text type="secondary">producthub · 邀请链接</Typography.Text>
        </div>

        {!token ? (
          <Alert
            type="error"
            showIcon
            message="邀请链接无效"
            description="链接缺少邀请码,请使用邮件中的完整链接,或联系管理员重新发送。"
          />
        ) : (
          <Form<FormValues>
            layout="vertical"
            onFinish={handleFinish}
            requiredMark={false}
          >
            <Form.Item
              name="new_password"
              rules={[
                { required: true, message: '请设置密码' },
                { min: 8, max: 72, message: '密码长度为 8–72 位' },
              ]}
            >
              <Input.Password
                prefix={<LockOutlined />}
                placeholder="设置密码(8–72 位)"
                autoComplete="new-password"
                size="large"
              />
            </Form.Item>
            <Form.Item
              name="confirm"
              dependencies={['new_password']}
              rules={[
                { required: true, message: '请再次输入密码' },
                ({ getFieldValue }) => ({
                  validator(_, value) {
                    if (!value || getFieldValue('new_password') === value) {
                      return Promise.resolve()
                    }
                    return Promise.reject(new Error('两次输入的密码不一致'))
                  },
                }),
              ]}
            >
              <Input.Password
                prefix={<LockOutlined />}
                placeholder="确认密码"
                autoComplete="new-password"
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
              设置并进入
            </Button>
          </Form>
        )}
      </Card>
    </div>
  )
}
