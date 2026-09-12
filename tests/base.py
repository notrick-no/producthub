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

import bcrypt

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

# 默认管理员:每个用例 setUp 都会建一个并让 self.client 真实登录,
# 这样第三版加登录门禁后,既有 65 条直打 /api 的用例不用逐个改。
# 低轮数哈希只为测试提速(rounds=4,bcrypt 允许的最小轮数)。
_ADMIN_EMAIL = "admin@test.local"
_ADMIN_PASSWORD = "AdminTest2026"


def _fast_hash(password: str) -> str:
    return bcrypt.hashpw(
        password.encode("utf-8"), bcrypt.gensalt(rounds=4)
    ).decode("utf-8")


_ADMIN_HASH = _fast_hash(_ADMIN_PASSWORD)


class ApiTestCase(unittest.TestCase):
    """每个测试类重建一次表结构,每个用例前清空数据。"""

    @classmethod
    def setUpClass(cls) -> None:
        models.Base.metadata.drop_all(bind=engine)
        models.Base.metadata.create_all(bind=engine)

    def setUp(self) -> None:
        self.client = TestClient(app)
        self._truncate_all()
        self._seed_default_admin()

    # ---------- 数据清理 ----------
    def _truncate_all(self) -> None:
        """清空全部表并重置自增 id,保证每个用例从空白库开始。

        表名从 metadata 推导,不写死列表 —— 原来写死时,新加一张表忘了往里加,
        数据就会在用例之间泄漏,症状是「某个用例偶发失败」,极难查。
        现在 models.py 一加表,这里自动跟上。
        """
        tables = ", ".join(t.name for t in models.Base.metadata.sorted_tables)
        with SessionLocal() as db:
            db.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
            db.commit()

    # ---------- 鉴权(第三版)----------
    def _seed_default_admin(self) -> None:
        """库中直插一个默认管理员并让 self.client 登录(拿真实会话 cookie)。"""
        self._add_user(
            email=_ADMIN_EMAIL,
            name="测试管理员",
            role="admin",
            password_hash=_ADMIN_HASH,
        )
        r = self.client.post(
            "/api/auth/login",
            json={"email": _ADMIN_EMAIL, "password": _ADMIN_PASSWORD},
        )
        self.assertEqual(r.status_code, 200, r.text)

    def _add_user(
        self,
        email: str,
        name: str = "测试员工",
        role: str = "employee",
        password: str | None = None,
        password_hash: str | None = None,
        is_active: bool = True,
        must_change_password: bool = False,
        department: str | None = None,
    ) -> int:
        """DB 直插一个账号,返回其 id(不走接口,方便造场景)。"""
        with SessionLocal() as db:
            user = models.User(
                email=email.strip().lower(),
                name=name,
                role=role,
                department=department,
                is_active=is_active,
                must_change_password=must_change_password,
                password_hash=password_hash or (
                    _fast_hash(password) if password else None
                ),
            )
            db.add(user)
            db.commit()
            return user.id

    def login_client(self, email: str, password: str) -> TestClient:
        """另起一个已登录的客户端(扮演普通员工/第二个管理员)。"""
        client = TestClient(app)
        r = client.post("/api/auth/login", json={"email": email, "password": password})
        self.assertEqual(r.status_code, 200, r.text)
        return client

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

    def new_requirement(self, description: str = "示例需求", **fields) -> dict:
        """POST 新建一条需求,断言 201 后返回响应 JSON。"""
        r = self.client.post(
            "/api/requirements", json={"description": description, **fields}
        )
        self.assertEqual(r.status_code, 201, r.text)
        return r.json()

    def new_comment(
        self,
        target_type: str,
        target_id: int,
        body: str = "示例评论",
        client: TestClient | None = None,
        **fields,
    ) -> dict:
        """POST 发一条评论(默认以 self.client 的身份),断言 201 后返回响应 JSON。

        client 传另一个已登录客户端 = 以别人的身份发,用于「删别人的评论」这类用例。
        """
        r = (client or self.client).post(
            "/api/comments",
            json={
                "target_type": target_type,
                "target_id": target_id,
                "body": body,
                **fields,
            },
        )
        self.assertEqual(r.status_code, 201, r.text)
        return r.json()

    def new_post(
        self,
        title: str = "示例帖子",
        status: str = "draft",
        client: TestClient | None = None,
        **fields,
    ) -> dict:
        """POST 新建一篇帖子(默认草稿),断言 201 后返回响应 JSON。

        client 传另一个已登录客户端 = 以别人的身份发,用于草稿可见性这类用例。
        """
        r = (client or self.client).post(
            "/api/blog", json={"title": title, "status": status, **fields}
        )
        self.assertEqual(r.status_code, 201, r.text)
        return r.json()

    def new_tag(self, name: str = "默认标签") -> dict:
        """POST 新建一个博客标签,断言 201 后返回响应 JSON。"""
        r = self.client.post("/api/blog/tags", json={"name": name})
        self.assertEqual(r.status_code, 201, r.text)
        return r.json()
