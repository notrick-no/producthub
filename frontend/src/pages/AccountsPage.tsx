import { useCallback, useEffect, useState } from 'react'
import {
  Alert,
  App as AntApp,
  Button,
  Form,
  Input,
  Modal,
  Popconfirm,
  Space,
  Table,
  Tag,
  Typography,
} from 'antd'
import type { TableColumnsType } from 'antd'
import { LockOutlined, StopOutlined, UserAddOutlined } from '@ant-design/icons'
import {
  createUser,
  listUsers,
  resetUserPassword,
  updateUser,
} from '../api/resources'
import type { User, UserPayload } from '../types'
import { useAuth } from '../auth/AuthContext'
import { formatFullDate } from '../format'

/** 管理员账号管理:列表 + 新建(邀请邮件)+ 启停 + 重置密码。 */
export default function AccountsPage() {
  const { message } = AntApp.useApp()
  const { user: me } = useAuth()
  const [form] = Form.useForm<UserPayload>()

  const [users, setUsers] = useState<User[]>([])
  const [loading, setLoading] = useState(true)
  const [creating, setCreating] = useState(false)
  const [createOpen, setCreateOpen] = useState(false)

  const reload = useCallback(async () => {
    setLoading(true)
    try {
      setUsers(await listUsers())
    } catch (err) {
      message.error(err instanceof Error ? err.message : '加载账号失败')
    } finally {
      setLoading(false)
    }
  }, [message])

  useEffect(() => {
    reload()
  }, [reload])

  const handleCreate = async () => {
    const values = await form.validateFields()
    setCreating(true)
    try {
      await createUser(values)
      message.success(`已创建账号,邀请邮件将发送到 ${values.email.trim().toLowerCase()}`)
      setCreateOpen(false)
      form.resetFields()
      await reload()
    } catch (err) {
      message.error(err instanceof Error ? err.message : '创建失败')
    } finally {
      setCreating(false)
    }
  }

  const handleToggle = async (u: User, active: boolean) => {
    try {
      await updateUser(u.id, { is_active: active })
      message.success(
        active ? `已启用 ${u.name}` : `已停用 ${u.name}(该员工已被登出)`,
      )
      await reload()
    } catch (err) {
      message.error(err instanceof Error ? err.message : '操作失败')
    }
  }

  const handleReset = async (u: User) => {
    try {
      await resetUserPassword(u.id)
      message.success(`已重置 ${u.email},临时密码已发送到该邮箱`)
      await reload()
    } catch (err) {
      message.error(err instanceof Error ? err.message : '重置失败')
    }
  }

  const columns: TableColumnsType<User> = [
    {
      title: '姓名',
      dataIndex: 'name',
      render: (_, u) => (
        <Space size={6}>
          <Typography.Text strong>{u.name}</Typography.Text>
          {u.id === me?.id && <Tag color="blue">我</Tag>}
        </Space>
      ),
    },
    { title: '邮箱', dataIndex: 'email', ellipsis: true },
    {
      title: '部门',
      dataIndex: 'department',
      width: 120,
      render: (v: string | null) => v || '—',
    },
    {
      title: '角色',
      dataIndex: 'role',
      width: 90,
      render: (r: User['role']) =>
        r === 'admin' ? <Tag color="gold">管理员</Tag> : <Tag>员工</Tag>,
    },
    {
      title: '状态',
      dataIndex: 'is_active',
      width: 90,
      render: (active: boolean) =>
        active ? <Tag color="green">启用</Tag> : <Tag color="red">已停用</Tag>,
    },
    {
      title: '密码',
      dataIndex: 'password_set',
      width: 90,
      render: (set: boolean) =>
        set ? <Typography.Text type="secondary">已设置</Typography.Text> : <Tag color="orange">待邀请</Tag>,
    },
    {
      title: '创建时间',
      dataIndex: 'created_at',
      width: 110,
      render: (v: string) => formatFullDate(v),
    },
    {
      title: '操作',
      key: 'actions',
      width: 170,
      render: (_, u) => {
        const isSelf = u.id === me?.id
        return (
          <Space size={4}>
            {isSelf ? (
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                当前账号
              </Typography.Text>
            ) : u.is_active ? (
              <>
                <Popconfirm
                  title={`停用「${u.name}」?`}
                  description="离职冻结:该员工会被立即登出,且无法再登录。"
                  okText="停用"
                  okButtonProps={{ danger: true }}
                  onConfirm={() => handleToggle(u, false)}
                >
                  <Button type="link" danger size="small" icon={<StopOutlined />}>
                    停用
                  </Button>
                </Popconfirm>
                <Popconfirm
                  title={`重置「${u.name}」的密码?`}
                  description="将生成临时密码并发送到该邮箱,其当前登录会被登出,首次登录需改密。"
                  okText="重置"
                  onConfirm={() => handleReset(u)}
                >
                  <Button type="link" size="small" icon={<LockOutlined />}>
                    重置密码
                  </Button>
                </Popconfirm>
              </>
            ) : (
              <Button
                type="link"
                size="small"
                onClick={() => handleToggle(u, true)}
              >
                启用
              </Button>
            )}
          </Space>
        )
      },
    },
  ]

  return (
    <>
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          marginBottom: 16,
        }}
      >
        <Space direction="vertical" size={0}>
          <Typography.Title level={5} style={{ margin: 0 }}>
            员工账号
          </Typography.Title>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            管理员通过邮件邀请员工加入;登录后共享同一份产品研究数据。
          </Typography.Text>
        </Space>
        <Button
          type="primary"
          icon={<UserAddOutlined />}
          onClick={() => setCreateOpen(true)}
        >
          新建账号
        </Button>
      </div>

      <Table<User>
        rowKey="id"
        columns={columns}
        dataSource={users}
        loading={loading}
        pagination={{ pageSize: 20, showTotal: (t) => `共 ${t} 个账号` }}
        scroll={{ x: 900 }}
      />

      <Modal
        title="新建员工账号"
        open={createOpen}
        onCancel={() => setCreateOpen(false)}
        onOk={handleCreate}
        okText="创建并发送邀请"
        confirmLoading={creating}
        destroyOnHidden
      >
        <Alert
          type="info"
          showIcon
          style={{ marginBottom: 16 }}
          message="创建后系统会发送一封邀请邮件到该邮箱,员工点邮件里的链接自助设置密码(链接 72 小时内有效)。"
        />
        <Form<UserPayload>
          form={form}
          layout="vertical"
          requiredMark={false}
          initialValues={{ department: undefined }}
        >
          <Form.Item
            name="name"
            label="姓名"
            rules={[{ required: true, whitespace: true, message: '请输入姓名' }]}
          >
            <Input placeholder="员工姓名" maxLength={255} autoFocus />
          </Form.Item>
          <Form.Item
            name="email"
            label="邮箱"
            rules={[
              { required: true, message: '请输入邮箱' },
              { type: 'email', message: '请输入有效邮箱' },
            ]}
          >
            <Input placeholder="登录邮箱(将作为登录账号)" maxLength={255} />
          </Form.Item>
          <Form.Item name="department" label="部门(可选)">
            <Input placeholder="如市场部 / 产品部" maxLength={100} />
          </Form.Item>
        </Form>
      </Modal>
    </>
  )
}
