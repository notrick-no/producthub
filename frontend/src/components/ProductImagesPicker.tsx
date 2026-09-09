import { useEffect, useRef, useState } from 'react'
import { App as AntApp, Button, Tooltip, Typography, Upload } from 'antd'
import { CloseOutlined, PictureOutlined } from '@ant-design/icons'

const { Dragger } = Upload

const MAX_IMAGE_SIZE = 10 * 1024 * 1024 // 与后端一致:单张 ≤ 10 MB

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
  const [files, setFiles] = useState<File[]>([])
  const urls = useRef(new Map<File, string>())

  // 每张缩略图一个 objectURL;组件卸载时统一回收
  useEffect(
    () => () => {
      urls.current.forEach((url) => URL.revokeObjectURL(url))
    },
    [],
  )

  const urlOf = (f: File): string => {
    let url = urls.current.get(f)
    if (!url) {
      url = URL.createObjectURL(f)
      urls.current.set(f, url)
    }
    return url
  }

  const commit = (next: File[]) => {
    setFiles(next)
    onChange(next)
  }

  const addFiles = (incoming: File[]) => {
    const ok: File[] = []
    for (const f of incoming) {
      if (!f.type.startsWith('image/')) {
        message.warning(`${f.name}: 只支持图片`)
        continue
      }
      if (f.size > MAX_IMAGE_SIZE) {
        message.warning(`${f.name}: 不能超过 10 MB`)
        continue
      }
      if (!files.some((x) => x === f)) ok.push(f) // 已选的不重复加
    }
    if (ok.length) commit([...files, ...ok])
  }

  const remove = (f: File) => {
    const url = urls.current.get(f)
    if (url) {
      URL.revokeObjectURL(url)
      urls.current.delete(f)
    }
    commit(files.filter((x) => x !== f))
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

      {files.length > 0 && (
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginTop: 12 }}>
          {files.map((f) => (
            <div
              key={urlOf(f)}
              style={{
                position: 'relative',
                border: '1px solid #f0f0f0',
                borderRadius: 6,
                padding: 2,
                background: '#fff',
              }}
            >
              <img
                src={urlOf(f)}
                alt={f.name}
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
                  onClick={() => remove(f)}
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
                {f.name}
              </Typography.Text>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
