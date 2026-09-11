import { useEffect, useRef, useState } from 'react'
import { App as AntApp, Button, Tooltip, Typography, Upload } from 'antd'
import { CloseOutlined, PictureOutlined } from '@ant-design/icons'

const { Dragger } = Upload

const MAX_IMAGE_SIZE = 10 * 1024 * 1024 // 与后端一致:单张 ≤ 10 MB

/** 已选中的一张图:文件本身 + 它的预览地址。 */
interface PickedImage {
  file: File
  /**
   * objectURL。在「选中」那一刻建好,渲染时只读。
   * 原来是在渲染期现建现取(往 ref 里写),那样渲染就不再是纯的 ——
   * React 可能渲染一遍又丢掉,丢掉的那遍就漏一个 objectURL 出去。
   */
  previewUrl: string
}

interface Props {
  /** 每次增删后把当前选中的文件数组交给父级;父级在保存后用它逐张上传。 */
  onChange: (files: File[]) => void
}

/**
 * 新建记录时的「选图」区:只把图片暂存到本地做预览,**不真正上传**
 * (后端图片上传要产品先存在,真正上传发生在「保存」创建出产品之后)。
 * 选图支持点击选择与拖拽、可多选、单张 ≤ 10 MB;每张可单独移除。
 */
export default function ProductImagesPicker({ onChange }: Props) {
  const { message } = AntApp.useApp()
  const [picked, setPicked] = useState<PickedImage[]>([])

  // 卸载时回收还没被移除的那些 objectURL。
  // 用 ref 记当前值,是为了让卸载清理只挂一次(依赖为空),不必每次增删重挂。
  const pickedRef = useRef<PickedImage[]>([])
  useEffect(() => {
    pickedRef.current = picked
  }, [picked])
  useEffect(
    () => () => {
      pickedRef.current.forEach((p) => URL.revokeObjectURL(p.previewUrl))
    },
    [],
  )

  const commit = (next: PickedImage[]) => {
    setPicked(next)
    onChange(next.map((p) => p.file))
  }

  const addFiles = (incoming: File[]) => {
    const ok: PickedImage[] = []
    for (const file of incoming) {
      if (!file.type.startsWith('image/')) {
        message.warning(`${file.name}: 只支持图片`)
        continue
      }
      if (file.size > MAX_IMAGE_SIZE) {
        message.warning(`${file.name}: 不能超过 10 MB`)
        continue
      }
      if (!picked.some((p) => p.file === file)) {
        ok.push({ file, previewUrl: URL.createObjectURL(file) }) // 已选的不重复加
      }
    }
    if (ok.length) commit([...picked, ...ok])
  }

  const remove = (target: PickedImage) => {
    URL.revokeObjectURL(target.previewUrl)
    commit(picked.filter((p) => p !== target))
  }

  return (
    <div>
      <Dragger
        multiple
        accept="image/*"
        showUploadList={false}
        fileList={[]}
        beforeUpload={(file) => {
          addFiles([file as File])
          return Upload.LIST_IGNORE // 只收文件,不让 antd 自己上传
        }}
      >
        <p style={{ margin: 0 }}>
          <PictureOutlined style={{ marginRight: 6 }} />
          点击或拖拽选择截图(可多选)· 保存记录时随之一并上传
        </p>
      </Dragger>

      {picked.length > 0 && (
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginTop: 12 }}>
          {picked.map((p) => (
            <div
              key={p.previewUrl}
              style={{
                position: 'relative',
                border: '1px solid #f0f0f0',
                borderRadius: 6,
                padding: 2,
                background: '#fff',
              }}
            >
              <img
                src={p.previewUrl}
                alt={p.file.name}
                width={96}
                height={64}
                style={{ objectFit: 'cover', borderRadius: 4, display: 'block' }}
              />
              <Tooltip title="移除这张">
                <Button
                  type="text"
                  size="small"
                  danger
                  icon={<CloseOutlined />}
                  style={{ position: 'absolute', top: 2, right: 2 }}
                  onClick={() => remove(p)}
                />
              </Tooltip>
              <Typography.Text
                type="secondary"
                style={{
                  position: 'absolute',
                  bottom: 4,
                  left: 6,
                  right: 6,
                  color: '#fff',
                  fontSize: 10,
                  overflow: 'hidden',
                  textOverflow: 'ellipsis',
                  whiteSpace: 'nowrap',
                  textShadow: '0 1px 2px rgba(0,0,0,.6)',
                }}
              >
                {p.file.name}
              </Typography.Text>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
