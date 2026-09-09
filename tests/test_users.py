"""管理员账号管理(第三版):员工无权、创建邀请、重复邮箱、停用、重置、护栏。

测试里把 app.mailer 的「是否已配置 / 发信函数」整模块 mock 掉,
不真正发邮件,只断言端点行为与发信调用本身。
"""
from unittest import mock

from sqlalchemy import select

from base import ApiTestCase, _ADMIN_EMAIL, _ADMIN_PASSWORD
from app.db import SessionLocal
from app.models import User
from app.security import hash_token

_EMP = "emp@test.local"
_EMP_PW = "EmpPass2026"


def _fetch(email: str) -> User | None:
    with SessionLocal() as db:
        return db.scalars(select(User).where(User.email == email)).one_or_none()


class UsersAdminTestCase(ApiTestCase):
    """self.client = 已登录的默认管理员。"""

    def _admin_id(self) -> int:
        r = self.client.get("/api/users")
        self.assertEqual(r.status_code, 200, r.text)
        return next(u["id"] for u in r.json() if u["email"] == _ADMIN_EMAIL)

    # ---------- 权限 ----------

    def test_anonymous_cannot_list_users(self):
        from fastapi.testclient import TestClient
        from app.main import app

        self.assertEqual(TestClient(app).get("/api/users").status_code, 401)

    def test_employee_cannot_access_users(self):
        self._add_user(email=_EMP, password=_EMP_PW)
        emp = self.login_client(_EMP, _EMP_PW)
        self.assertEqual(emp.get("/api/users").status_code, 403)
        r = emp.post("/api/users", json={"name": "张三", "email": "a@b.com"})
        self.assertEqual(r.status_code, 403, r.text)

    # ---------- 创建 + 邀请 ----------

    def test_create_employee_sends_invite_and_can_set_password(self):
        with (
            mock.patch("app.mailer.is_mail_configured", return_value=True),
            mock.patch("app.mailer.send_user_invite") as mock_send,
        ):
            r = self.client.post(
                "/api/users",
                json={
                    "name": " 李雷 ",
                    "email": "LiLei@Test.LOCAL ",  # 带大小写与空白,应归一
                    "department": "市场部",
                },
            )
        self.assertEqual(r.status_code, 201, r.text)
        body = r.json()
        self.assertEqual(body["email"], "lilei@test.local")
        self.assertEqual(body["name"], "李雷")
        self.assertEqual(body["role"], "employee")
        self.assertFalse(body["password_set"])

        # 发了一封邀请邮件,带明文一次性 token
        self.assertEqual(mock_send.call_count, 1)
        sent_email, sent_name, raw_token = mock_send.call_args.args
        self.assertEqual((sent_email, sent_name), ("lilei@test.local", "李雷"))

        # 库里该账号只存 token 哈希、未设密码
        user = _fetch("lilei@test.local")
        self.assertIsNotNone(user)
        self.assertEqual(user.invite_token_hash, hash_token(raw_token))
        self.assertIsNone(user.password_hash)
        self.assertFalse(user.must_change_password)

        # 员工用邀请 token 自设密码 → 直接登录并可用业务
        inv = _new_client()
        r = inv.post(
            "/api/auth/set-password",
            json={"token": raw_token, "new_password": _EMP_PW},
        )
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(inv.get("/api/products").status_code, 200)
        # 再次用临时邀请码登录(此时密码已设为 _EMP_PW)
        emp = self.login_client("lilei@test.local", _EMP_PW)
        self.assertEqual(emp.get("/api/products").status_code, 200)

    def test_duplicate_email_409_even_with_different_case(self):
        with (
            mock.patch("app.mailer.is_mail_configured", return_value=True),
            mock.patch("app.mailer.send_user_invite") as mock_send,
        ):
            ok = self.client.post(
                "/api/users", json={"name": "李雷", "email": "lilei@test.local"}
            )
            self.assertEqual(ok.status_code, 201, ok.text)
            dup = self.client.post(
                "/api/users", json={"name": "李雷", "email": "LiLei@Test.LOCAL"}
            )
        self.assertEqual(dup.status_code, 409, dup.text)
        self.assertIn("已注册", dup.json()["detail"])
        self.assertEqual(mock_send.call_count, 1)  # 重复创建不该再发信

    def test_create_rejected_when_smtp_unconfigured(self):
        with mock.patch("app.mailer.is_mail_configured", return_value=False):
            r = self.client.post(
                "/api/users", json={"name": "王五", "email": "wang@test.local"}
            )
        self.assertEqual(r.status_code, 503, r.text)
        # 没有留下半截账号
        self.assertIsNone(_fetch("wang@test.local"))

    # ---------- 列表 ----------

    def test_list_users_contains_admin_and_employees(self):
        self._add_user(email=_EMP, password=_EMP_PW, name="李雷")
        r = self.client.get("/api/users")
        self.assertEqual(r.status_code, 200, r.text)
        emails = {u["email"] for u in r.json()}
        self.assertEqual(emails, {_ADMIN_EMAIL, _EMP})
        admin = next(u for u in r.json() if u["email"] == _ADMIN_EMAIL)
        self.assertTrue(admin["password_set"])

    # ---------- 停用 / 启用 ----------

    def test_disable_kicks_offline_and_enable_allows_relogin(self):
        uid = self._add_user(email=_EMP, password=_EMP_PW)
        emp = self.login_client(_EMP, _EMP_PW)
        self.assertEqual(emp.get("/api/products").status_code, 200)

        r = self.client.patch(f"/api/users/{uid}", json={"is_active": False})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertFalse(r.json()["is_active"])
        # 已被踢下线,会话作废
        self.assertEqual(emp.get("/api/products").status_code, 401)

        # 启用后可重新登录
        r = self.client.patch(f"/api/users/{uid}", json={"is_active": True})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(r.json()["is_active"])
        self.assertEqual(self.login_client(_EMP, _EMP_PW).get("/api/products").status_code, 200)

    def test_employee_cannot_login_when_disabled(self):
        uid = self._add_user(email=_EMP, password=_EMP_PW)
        self.client.patch(f"/api/users/{uid}", json={"is_active": False})
        r = self.client.post(
            "/api/auth/login", json={"email": _EMP, "password": _EMP_PW}
        )
        self.assertEqual(r.status_code, 401, r.text)

    # ---------- 护栏:最后一个启用中的管理员 ----------

    def test_cannot_disable_last_active_admin(self):
        admin_id = self._admin_id()
        r = self.client.patch(f"/api/users/{admin_id}", json={"is_active": False})
        self.assertEqual(r.status_code, 400, r.text)
        self.assertIn("最后一个启用", r.json()["detail"])

    def test_cannot_reset_last_active_admin(self):
        admin_id = self._admin_id()
        r = self.client.post(f"/api/users/{admin_id}/reset-password")
        self.assertEqual(r.status_code, 400, r.text)
        self.assertIn("最后一个启用", r.json()["detail"])

    def test_disable_second_admin_allowed(self):
        second_id = self._add_user(
            email="admin2@test.local", name="二号管理员", role="admin", password=_EMP_PW
        )
        r = self.client.patch(f"/api/users/{second_id}", json={"is_active": False})
        self.assertEqual(r.status_code, 200, r.text)

    # ---------- 重置密码 ----------

    def test_reset_password_forces_change_and_invalidates_old_session(self):
        uid = self._add_user(email=_EMP, password=_EMP_PW)
        emp = self.login_client(_EMP, _EMP_PW)
        self.assertEqual(emp.get("/api/products").status_code, 200)

        with (
            mock.patch("app.mailer.is_mail_configured", return_value=True),
            mock.patch("app.mailer.send_reset_password") as mock_send,
        ):
            r = self.client.post(f"/api/users/{uid}/reset-password")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["ok"], True)

        # 发了临时密码邮件
        self.assertEqual(mock_send.call_count, 1)
        temp = mock_send.call_args.args[2]  # (email, name, temp_password)

        # 旧会话被踢掉
        self.assertEqual(emp.get("/api/products").status_code, 401)

        # 临时密码能登录,但必须改密 → 业务被硬门禁挡住
        emp = self.login_client(_EMP, temp)
        me = emp.get("/api/auth/me")
        self.assertTrue(me.json()["must_change_password"])
        self.assertEqual(emp.get("/api/products").status_code, 403)

        # 改完密码放行,原临时密码失效
        r = emp.post(
            "/api/auth/password",
            json={"old_password": temp, "new_password": "BrandNew2026"},
        )
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(emp.get("/api/products").status_code, 200)
        self.assertEqual(
            self.client.post(
                "/api/auth/login", json={"email": _EMP, "password": temp}
            ).status_code,
            401,
        )

    def test_reset_rejected_when_smtp_unconfigured(self):
        uid = self._add_user(email=_EMP, password=_EMP_PW)
        with mock.patch("app.mailer.is_mail_configured", return_value=False):
            r = self.client.post(f"/api/users/{uid}/reset-password")
        self.assertEqual(r.status_code, 503, r.text)

    def test_patch_update_name_and_department(self):
        uid = self._add_user(email=_EMP, password=_EMP_PW, name="旧名")
        r = self.client.patch(
            f"/api/users/{uid}", json={"name": "新名", "department": ""}
        )
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["name"], "新名")
        self.assertIsNone(r.json()["department"])  # 空白部门清空

    def test_patch_missing_user_404(self):
        r = self.client.patch("/api/users/999999", json={"name": "x"})
        self.assertEqual(r.status_code, 404, r.text)


def _new_client():
    from fastapi.testclient import TestClient

    from app.main import app

    return TestClient(app)
