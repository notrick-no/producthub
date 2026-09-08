"""上传文件的本地存储配置。

设计约定(见 第二版产品-开发中.md):
  - 数据库只存图片元数据与访问路径,**不存二进制**
  - 文件本体放在项目根 `uploads/` 目录,FastAPI 以 /uploads 静态托管
  - 用 UPLOAD_DIR 环境变量可覆盖(测试指向临时目录,不碰真实文件)

访问路径与磁盘的一一对应:
  DB `path` 存 "/uploads/<随机名>.<扩展名>";浏览器直接拿这个当图片 src。
"""
import os
from pathlib import Path

# 项目根目录的 uploads/(tests.base 会在 import 前用环境变量指向临时目录)
UPLOAD_DIR = Path(
    os.environ.get(
        "UPLOAD_DIR",
        Path(__file__).resolve().parent.parent / "uploads",
    )
)
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

# 允许的类型(用真实 content_type 取扩展名,不信任用户给的文件名)
ALLOWED_IMAGE_TYPES = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "image/webp": ".webp",
}

# 单张上限 10 MB
MAX_IMAGE_SIZE = 10 * 1024 * 1024


def image_ext(content_type: str) -> str | None:
    """合法图片类型 → 落盘扩展名;否则 None。"""
    return ALLOWED_IMAGE_TYPES.get(content_type)
