import { useState } from 'react'
import {
  App as AntApp,
  Button,
  Popconfirm,
  Space,
  Spin,
  Typography,
  Upload,
} from 'antd'
import { DeleteOutlined, PictureOutlined } from '@ant-design/icons'
import type { ProductImage } from '../types'
import { deleteProductImage, uploadProductImage } from '../api/resources'

const { Dragger } = Upload

interface Props {
  productId: number
  images: ProductImage[]
  /** 任意上传/删除成功后调用,重新拉取产品(拿到最新 images)。 */
  onReload: () => void
}

/** 产品素材卡片:点击/拖拽上传、多图、点图预览、删除(无排序、无说明)。 */
export default function ProductImagesCard({ productId, images, onReload }: Props) {
  const { message } = AntApp.useApp()
  const [busy, setBusy] = useState(false) // 正在上传/删除

  const uploadOne = async (file: File) => {
    setBusy(true)
    try {
      await uploadProductImage(productId, file)
      onReload()
    } catch (err) {
      message.error(`${file.name} 上传失败: ${err instanceof Error ? err.message : '未知错误'}`)
    } finally {
      setBusy(false)
    }
  }

  const handleDelete = async (img: ProductImage) => {
    setBusy(true)
    try {
      await deleteProductImage(productId, img.id)
      onReload()
    } catch (err) {
      message.error(err instanceof Error ? err.message : '删除失败')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div style={{ position: 'relative' }}>
      <Dragger
        multiple
        accept="image/*"
        showUploadList={false}
        disabled={busy}
        customRequest={({ file, onSuccess, onError }) => {
          uploadOne(file as File)
            .then(() => onSuccess?.(file))
            .catch((err) => onError?.(err as Error))
        }}
      >
        <p style={{ margin: 0 }}>
          <PictureOutlined style={{ marginRight: 6 }} />
          点击或拖拽上传图片(可多选),点图片可预览
        </p>
      </Dragger>

      {busy && (
        <div
          style={{
            position: 'absolute',
            inset: 0,
            zIndex: 2,
            background: 'rgba(255,255,255,.45)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
          }}
        >
          <Spin />
        </div>
      )}

      {images.length === 0 ? (
        <Typography.Text type="secondary" style={{ display: 'block', marginTop: 12 }}>
          还没有素材图片
        </Typography.Text>
      ) : (
        <Space size={8} wrap style={{ marginTop: 12 }}>
          {images.map((img) => (
            <div
              key={img.id}
              style={{
                position: 'relative',
                border: '1px solid #f0f0f0',
                borderRadius: 8,
                padding: 4,
                background: '#fff',
              }}
            >
              <img
                src={img.path}
                alt={img.filename || ''}
                width={148}
                height={98}
                style={{
                  objectFit: 'cover',
                  borderRadius: 6,
                  display: 'block',
                  cursor: 'pointer',
                }}
                title={img.filename ? `点开预览: ${img.filename}` : '点开预览'}
                onClick={() => window.open(img.path, '_blank')}
              />
              <Popconfirm
                title="删除这张图片?"
                description="图片文件会一并删除,不可恢复。"
                okText="删除"
                okButtonProps={{ danger: true }}
                onConfirm={() => handleDelete(img)}
              >
                <Button
                  type="text"
                  size="small"
                  danger
                  icon={<DeleteOutlined />}
                  style={{ position: 'absolute', top: 8, right: 8 }}
                />
              </Popconfirm>
            </div>
          ))}
        </Space>
      )}
    </div>
  )
}
