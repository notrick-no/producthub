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
import { DeleteOutlined, HolderOutlined, PictureOutlined } from '@ant-design/icons'
import {
  DndContext,
  KeyboardSensor,
  PointerSensor,
  closestCenter,
  useSensor,
  useSensors,
} from '@dnd-kit/core'
import type { DragEndEvent } from '@dnd-kit/core'
import {
  SortableContext,
  arrayMove,
  rectSortingStrategy,
  sortableKeyboardCoordinates,
  useSortable,
} from '@dnd-kit/sortable'
import { CSS } from '@dnd-kit/utilities'
import type { ProductImage } from '../types'
import {
  deleteProductImage,
  updateProductImage,
  uploadProductImage,
} from '../api/resources'

const { Dragger } = Upload

interface Props {
  productId: number
  images: ProductImage[]
  /** 任意增删改排成功后调用,重新拉取产品(拿到最新 images)。 */
  onReload: () => void
}

/** 一张可拖拽排序的图片磁贴。 */
function SortableImage({
  img,
  productId,
  onReload,
}: {
  img: ProductImage
  productId: number
  onReload: () => void
}) {
  const { message } = AntApp.useApp()
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } =
    useSortable({ id: img.id })
  const [deleting, setDeleting] = useState(false)

  const saveCaption = (value: string) => {
    const caption = value.trim() || null
    if (caption === img.caption) return
    updateProductImage(productId, img.id, { caption })
      .then(onReload)
      .catch((err) =>
        message.error(err instanceof Error ? err.message : '保存说明失败'),
      )
  }

  const handleDelete = async () => {
    setDeleting(true)
    try {
      await deleteProductImage(productId, img.id)
      onReload()
    } catch (err) {
      message.error(err instanceof Error ? err.message : '删除失败')
      setDeleting(false)
    }
  }

  return (
    <div
      ref={setNodeRef}
      style={{
        width: 148,
        transform: CSS.Transform.toString(transform),
        transition,
        opacity: isDragging ? 0.5 : 1,
      }}
    >
      <div
        {...attributes}
        {...listeners}
        title="拖动可排序,点图片可预览"
        style={{
          position: 'relative',
          border: '1px solid #f0f0f0',
          borderRadius: 8,
          padding: 4,
          cursor: 'grab',
          background: '#fff',
        }}
      >
        <img
          src={img.path}
          alt={img.caption || img.filename || ''}
          width={138}
          height={92}
          style={{ objectFit: 'cover', borderRadius: 6, display: 'block', cursor: 'pointer' }}
          onClick={() => window.open(img.path, '_blank')}
        />
        <HolderOutlined
          style={{
            position: 'absolute',
            top: 8,
            left: 8,
            color: '#fff',
            textShadow: '0 1px 2px rgba(0,0,0,.6)',
            pointerEvents: 'none',
          }}
        />
        <Typography.Text
          type="secondary"
          style={{
            position: 'absolute',
            bottom: 8,
            left: 8,
            color: '#fff',
            fontSize: 11,
            maxWidth: 100,
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            whiteSpace: 'nowrap',
            textShadow: '0 1px 2px rgba(0,0,0,.6)',
          }}
        >
          {img.filename || ''}
        </Typography.Text>
      </div>

      <Space size={6} style={{ width: '100%', justifyContent: 'space-between' }}>
        <Typography.Text
          type="secondary"
          style={{ fontSize: 12, flex: 1, minWidth: 0 }}
          ellipsis={{ tooltip: true }}
          editable={{
            onChange: saveCaption,
            tooltip: img.caption ? '编辑说明' : '添加说明',
          }}
        >
          {img.caption || '添加说明'}
        </Typography.Text>
        <Popconfirm
          title="删除这张图片?"
          description="图片文件会一并删除,不可恢复。"
          okText="删除"
          okButtonProps={{ danger: true }}
          onConfirm={handleDelete}
        >
          <Button
            type="text"
            size="small"
            danger
            loading={deleting}
            icon={<DeleteOutlined />}
          />
        </Popconfirm>
      </Space>
    </div>
  )
}

/** 产品素材卡片:上传(点击/拖拽/Ctrl+V)+ 多图预览 + 拖拽排序 + 说明/删除。 */
export default function ProductImagesCard({ productId, images, onReload }: Props) {
  const { message } = AntApp.useApp()
  const [busy, setBusy] = useState(0) // >0 表示正在上传/排序
  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 6 } }),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }),
  )

  const uploadOne = async (file: File) => {
    setBusy((n) => n + 1)
    try {
      await uploadProductImage(productId, file)
      onReload()
    } catch (err) {
      message.error(`${file.name} 上传失败: ${err instanceof Error ? err.message : '未知错误'}`)
    } finally {
      setBusy((n) => n - 1)
    }
  }

  // Ctrl+V 粘贴截图
  const handlePaste = (e: React.ClipboardEvent) => {
    const files = Array.from(e.clipboardData?.items ?? [])
      .filter((it) => it.kind === 'file' && it.type.startsWith('image/'))
      .map((it) => it.getAsFile())
      .filter((f): f is File => f !== null)
    if (files.length === 0) return
    e.preventDefault()
    files.forEach(uploadOne)
  }

  // 拖拽排序结束:按新顺序把 sort_order 重排后落库
  const handleDragEnd = async (e: DragEndEvent) => {
    const { active, over } = e
    if (!over || active.id === over.id) return
    const ids = images.map((i) => i.id)
    const from = ids.indexOf(Number(active.id))
    const to = ids.indexOf(Number(over.id))
    if (from < 0 || to < 0) return

    const next = arrayMove(images, from, to).map((img, index) => ({
      ...img,
      sort_order: index,
    }))
    const changed = images.filter((img) => {
      const moved = next.find((x) => x.id === img.id)
      return moved && moved.sort_order !== img.sort_order
    })
    if (changed.length === 0) return

    setBusy((n) => n + 1)
    try {
      await Promise.all(
        changed.map((img) =>
          updateProductImage(productId, img.id, { sort_order: img.sort_order }),
        ),
      )
      onReload()
    } catch (err) {
      message.error(err instanceof Error ? err.message : '排序保存失败')
    } finally {
      setBusy((n) => n - 1)
    }
  }

  return (
    <div onPaste={handlePaste}>
      <Dragger
        multiple
        accept="image/*"
        showUploadList={false}
        disabled={busy > 0}
        customRequest={({ file, onSuccess, onError }) => {
          uploadOne(file as File)
            .then(() => onSuccess?.(file))
            .catch((err) => onError?.(err as Error))
        }}
      >
        <p style={{ margin: 0 }}>
          <PictureOutlined style={{ marginRight: 6 }} />
          点击或拖拽上传图片 · 或直接 <b>Ctrl+V</b> 粘贴截图
        </p>
      </Dragger>

      <div style={{ position: 'relative', marginTop: 12 }}>
        {busy > 0 && (
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
          <Typography.Text type="secondary">还没有素材图片</Typography.Text>
        ) : (
          <DndContext
            sensors={sensors}
            collisionDetection={closestCenter}
            onDragEnd={handleDragEnd}
          >
            <SortableContext items={images.map((i) => i.id)} strategy={rectSortingStrategy}>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 12 }}>
                {images.map((img) => (
                  <SortableImage
                    key={img.id}
                    img={img}
                    productId={productId}
                    onReload={onReload}
                  />
                ))}
              </div>
            </SortableContext>
          </DndContext>
        )}
      </div>
    </div>
  )
}
