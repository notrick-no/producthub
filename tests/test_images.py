"""产品素材图片(ProductImage)接口测试:元数据 + uploads/ 文件落盘。

约定:
  - 数据库只存元数据,path 存 "/uploads/<随机名>.<扩展名>",浏览器可直接访问
  - tests.base 已把 UPLOAD_DIR 指到临时目录,这里可以放心读写文件
"""

from pathlib import Path

from app.db import SessionLocal
from app.storage import UPLOAD_DIR
from tests.base import ApiTestCase

# 一张极小的合法 PNG(1x1 像素),content-type 用 image/png
PNG_BYTES = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000d4944415478da63f8cfc0f01f0005000186fdb74a0000000049454e44ae426082"
)


def _upload(client, pid: int, filename="shot.png", content=PNG_BYTES, ctype="image/png"):
    return client.post(
        f"/api/products/{pid}/images",
        files={"file": (filename, content, ctype)},
    )


def _images_of(client, pid: int) -> list[dict]:
    return client.get(f"/api/products/{pid}").json()["images"]


def _count_rows() -> int:
    with SessionLocal() as db:
        from sqlalchemy import text

        return db.execute(text("SELECT count(*) FROM product_images")).scalar_one()


def _disk_files() -> list[str]:
    return [p.name for p in UPLOAD_DIR.iterdir() if p.is_file()]


class UploadTest(ApiTestCase):
    def test_upload_returns_metadata_and_stores_file(self):
        pid = self.new_product("Notion")["id"]
        r = _upload(self.client, pid)
        self.assertEqual(r.status_code, 201, r.text)
        img = r.json()
        self.assertEqual(img["filename"], "shot.png")
        self.assertEqual(img["content_type"], "image/png")
        self.assertEqual(img["size"], len(PNG_BYTES))
        # 已无说明/排序字段
        self.assertNotIn("caption", img)
        self.assertNotIn("sort_order", img)
        # path 是可访问的 /uploads/ 地址,文件确实落盘
        self.assertTrue(img["path"].startswith("/uploads/"))
        name = img["path"].rsplit("/", 1)[-1]
        self.assertIn(name, _disk_files())
        self.assertEqual((UPLOAD_DIR / name).read_bytes(), PNG_BYTES)

    def test_uploaded_file_servable(self):
        pid = self.new_product("Notion")["id"]
        img = _upload(self.client, pid).json()
        r = self.client.get(img["path"])
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.content, PNG_BYTES)

    def test_multiple_uploads_show_in_product_insertion_order(self):
        pid = self.new_product("Notion")["id"]
        first = _upload(self.client, pid, "a.png").json()
        second = _upload(self.client, pid, "b.png").json()
        # 图片无排序字段,展示顺序即录入顺序(按 id 升序)
        self.assertEqual([i["id"] for i in _images_of(self.client, pid)], [first["id"], second["id"]])

    def test_bad_uploads_rejected(self):
        pid = self.new_product("Notion")["id"]
        # 非图片类型
        r = _upload(self.client, pid, "doc.pdf", ctype="application/pdf")
        self.assertEqual(r.status_code, 400, r.text)
        # 空文件
        r = _upload(self.client, pid, "empty.png", b"", "image/png")
        self.assertEqual(r.status_code, 400, r.text)
        # 超过 10MB
        r = _upload(self.client, pid, "big.png", b"x" * (10 * 1024 * 1024 + 1), "image/png")
        self.assertEqual(r.status_code, 400, r.text)
        self.assertEqual(_count_rows(), 0)

    def test_upload_to_missing_product_404(self):
        r = _upload(self.client, 9999)
        self.assertEqual(r.status_code, 404, r.text)


class ImageDeleteTest(ApiTestCase):
    def test_delete_removes_row_and_disk_file(self):
        pid = self.new_product("Notion")["id"]
        img = _upload(self.client, pid).json()
        name = img["path"].rsplit("/", 1)[-1]
        self.assertIn(name, _disk_files())
        r = self.client.delete(f"/api/products/{pid}/images/{img['id']}")
        self.assertEqual(r.status_code, 204)
        self.assertEqual(_count_rows(), 0)
        self.assertNotIn(name, _disk_files())

    def test_delete_other_products_image_404(self):
        pid = self.new_product("Notion")["id"]
        other = self.new_product("别的")["id"]
        img = _upload(self.client, pid).json()
        r = self.client.delete(f"/api/products/{other}/images/{img['id']}")
        self.assertEqual(r.status_code, 404, r.text)


class ImageCascadeTest(ApiTestCase):
    def test_delete_product_cascades_image_rows(self):
        pid = self.new_product("Notion")["id"]
        _upload(self.client, pid)
        self.assertEqual(_count_rows(), 1)
        self.client.delete(f"/api/products/{pid}")
        self.assertEqual(_count_rows(), 0)

    def test_list_includes_images_in_insertion_order(self):
        pid = self.new_product("A")["id"]
        _upload(self.client, pid, "b.png")
        _upload(self.client, pid, "a.png")
        rows = self.client.get("/api/products").json()
        got = rows[0]["images"]
        self.assertEqual([i["filename"] for i in got], ["b.png", "a.png"])  # 按上传顺序
