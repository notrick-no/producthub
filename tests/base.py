"""测试基类:让测试跑在独立的 producthub_test 库,不碰开发数据。

用法(项目根目录):
    conda activate web
    python -m unittest discover -s tests -v

约定:
  - 导入本模块时,会先把 DATABASE_URL 指到 producthub_test,再导入 app.*
    (必须保证在任何测试文件 import app 之前先 import tests.base)
  - 每个测试类启动时重建表结构;每个用例前清空三张表并重置自增 id
  - 新增测试直接继承 ApiTestCase,用 self.client(TestClient)发请求
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

# 让项目根目录可被 import(任何 cwd 下都能跑)
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# 测试库连接串(可在运行前用环境变量 TEST_DATABASE_URL 覆盖)
TEST_DB_URL = os.getenv(
    "TEST_DATABASE_URL",
    "postgresql+psycopg://notrickno@localhost:5432/producthub_test",
)

# 必须在导入 app.db 之前覆盖连接串,否则引擎会连到开发库
os.environ["DATABASE_URL"] = TEST_DB_URL

# 上传目录也指向临时目录,测试不碰项目根的 uploads/(须在 import app 前设置)
_TMP_UPLOAD = tempfile.mkdtemp(prefix="ph-test-uploads-")
os.environ["UPLOAD_DIR"] = _TMP_UPLOAD

from sqlalchemy import text  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import models  # noqa: E402
from app.db import SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402


def _ensure_test_db_exists() -> None:
    """producthub_test 库不存在时创建(连接 postgres 库,需要超级用户权限)。"""
    import psycopg

    url = make_url(TEST_DB_URL)
    with psycopg.connect(
        host=url.host or "localhost",
        port=url.port or 5432,
        user=url.username,
        dbname="postgres",
        autocommit=True,
    ) as conn:
        exists = conn.execute(
            "SELECT 1 FROM pg_database WHERE datname = %s", (url.database,)
        ).fetchone()
        if not exists:
            conn.execute(f'CREATE DATABASE "{url.database}"')


_ensure_test_db_exists()


class ApiTestCase(unittest.TestCase):
    """每个测试类重建一次表结构,每个用例前清空数据。"""

    @classmethod
    def setUpClass(cls) -> None:
        models.Base.metadata.drop_all(bind=engine)
        models.Base.metadata.create_all(bind=engine)

    def setUp(self) -> None:
        self.client = TestClient(app)
        self._truncate_all()

    # ---------- 数据清理 ----------
    def _truncate_all(self) -> None:
        """清空全部表并重置自增 id,保证每个用例从空白库开始。"""
        with SessionLocal() as db:
            db.execute(
                text(
                    "TRUNCATE product_categories, product_milestones, "
                    "product_price_tiers, product_images, products, categories "
                    "RESTART IDENTITY CASCADE"
                )
            )
            db.commit()

    # ---------- 常用造数据助手 ----------
    def new_category(self, name: str = "默认分类", description: str | None = None) -> dict:
        """POST 新建一个分类,断言 201 后返回响应 JSON。"""
        r = self.client.post(
            "/api/categories", json={"name": name, "description": description}
        )
        self.assertEqual(r.status_code, 201, r.text)
        return r.json()

    def new_product(
        self,
        name: str = "示例产品",
        category_ids: list[int] | None = None,
        **fields,
    ) -> dict:
        """POST 新建一个产品(可选打标),断言 201 后返回响应 JSON。"""
        payload: dict = {"name": name, **fields}
        if category_ids is not None:
            payload["category_ids"] = category_ids
        r = self.client.post("/api/products", json=payload)
        self.assertEqual(r.status_code, 201, r.text)
        return r.json()
