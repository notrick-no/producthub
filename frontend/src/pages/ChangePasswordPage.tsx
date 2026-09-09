import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { App as AntApp, Button, Card, Form, Input, Typography } from 'antd'
import { LockOutlined } from '@ant-design/icons'
import { useAuth } from '../auth/AuthContext'
import { changePassword, fetchMe } from '../api/resources'

interface FormValues {
  old_password: string
  new_password: string
  confirm: string
}

/** 修改自己的密码(重置密码后首次登录会被门禁强制来这里)。 */
export default function ChangePasswordPage() {
  const { message } = AntApp.useApp()
  const navigate = useNavigate()
  const { setUser } = useAuth()
  const [submitting, setSubmitting] = useState(false)

  const handleFinish = async (values: FormValues) => {
    setSubmitting(true)
    try {
      await changePassword(values.old_password, values.new_password)
      // 刷新本地用户,把 must_change_password 置否,门禁放行回业务区
      const me = await fetchMe()
      setUser(me)
      message.success('密码已修改')
      navigate('/', { replace: true })
    } catch (err) {
      message.error(err instanceof Error ? err.message : '修改失败')
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
            修改密码
          </Typography.Title>
          <Typography.Text type="secondary">
            使用重置后的临时密码登录,需先设置新密码
          </Typography.Text>
        </div>
        <Form<FormValues>
          layout="vertical"
          onFinish={handleFinish}
          requiredMark={false}
          initialValues={{ old_password: '', new_password: '', confirm: '' }}
        >
          <Form.Item
            name="old_password"
            rules={[{ required: true, message: '请输入当前密码' }]}
          >
            <Input.Password
              prefix={<LockOutlined />}
              placeholder="当前密码(或重置后的临时密码)"
              autoComplete="current-password"
              size="large"
            />
          </Form.Item>
          <Form.Item
            name="new_password"
            rules={[
              { required: true, message: '请输入新密码' },
              { min: 8, max: 72, message: '密码长度为 8–72 位' },
            ]}
          >
            <Input.Password
              prefix={<LockOutlined />}
              placeholder="新密码(8–72 位)"
              autoComplete="new-password"
              size="large"
            />
          </Form.Item>
          <Form.Item
            name="confirm"
            dependencies={['new_password']}
            rules={[
              { required: true, message: '请再次输入新密码' },
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
              placeholder="确认新密码"
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
            确认修改
          </Button>
        </Form>
      </Card>
    </div>
  )
}
