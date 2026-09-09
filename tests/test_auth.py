"""登录 / 登出 / 当前用户 / 改密 / 邀请设密 / 首个管理员引导(第三版)。"""
import os
import unittest
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy import select

from base import ApiTestCase, _ADMIN_EMAIL, _ADMIN_PASSWORD, _fast_hash  # noqa: F401
from app.db import SessionLocal, engine
from app.main import app
from app.models import User
from app.models import Base
from app.security import hash_token

_EMP = "emp@test.local"
_EMP_PW = "EmpPass2026"


class AuthTestCase(ApiTestCase):
    """默认 self.client 已作为管理员登录;员工场景另起 login_client。"""

    def test_anonymous_blocked_from_business_api(self):
        anon = TestClient(app)
        r = anon.get("/api/products")
        self.assertEqual(r.status_code, 401, r.text)

    def test_login_wrong_password(self):
        self._add_user(email=_EMP, password=_EMP_PW)
        r = self.client.post(
            "/api/auth/login", json={"email": _EMP, "password": "WrongPass123"}
        )
        self.assertEqual(r.status_code, 401, r.text)
        self.assertIn("邮箱或密码不正确", r.json()["detail"])

    def test_login_email_case_insensitive(self):
        self._add_user(email=_EMP, password=_EMP_PW)
        r = self.client.post(
            "/api/auth/login", json={"email": "EMP@Test.LOCAL", "password": _EMP_PW}
        )
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["email"], _EMP)

    def test_login_disabled_account_401(self):
        self._add_user(email=_EMP, password=_EMP_PW, is_active=False)
        r = self.client.post(
            "/api/auth/login", json={"email": _EMP, "password": _EMP_PW}
        )
        self.assertEqual(r.status_code, 401, r.text)

    def test_me_unauthenticated_401(self):
        r = TestClient(app).get("/api/auth/me")
        self.assertEqual(r.status_code, 401, r.text)

    def test_me_returns_user_shape(self):
        r = self.client.get("/api/auth/me")
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(body["email"], _ADMIN_EMAIL)
        self.assertEqual(body["role"], "admin")
        self.assertNotIn("password_hash", body)

    def test_logout_clears_session(self):
        self.assertEqual(self.client.get("/api/auth/me").status_code, 200)
        r = self.client.post("/api/auth/logout")
        self.assertEqual(r.status_code, 204, r.text)
        self.assertEqual(self.client.get("/api/auth/me").status_code, 401)

    def test_change_own_password(self):
        self._add_user(email=_EMP, password=_EMP_PW)
        c = self.login_client(_EMP, _EMP_PW)
        r = c.post(
            "/api/auth/password",
            json={"old_password": _EMP_PW, "new_password": "BrandNew2026"},
        )
        self.assertEqual(r.status_code, 200, r.text)
        # 旧密码登不进,新密码能登
        self.assertEqual(
            self.client.post(
                "/api/auth/login", json={"email": _EMP, "password": _EMP_PW}
            ).status_code,
            401,
        )
        c2 = self.login_client(_EMP, "BrandNew2026")
        self.assertEqual(c2.get("/api/products").status_code, 200)
        # 改密后原登录端会话仍在(保留当前会话)
        self.assertEqual(c.get("/api/auth/me").status_code, 200)

    def test_change_password_wrong_old(self):
        self._add_user(email=_EMP, password=_EMP_PW)
        c = self.login_client(_EMP, _EMP_PW)
        r = c.post(
            "/api/auth/password",
            json={"old_password": "WrongOld2026", "new_password": "BrandNew2026"},
        )
        self.assertEqual(r.status_code, 400, r.text)

    def test_must_change_gate_blocks_business_until_changed(self):
        uid = self._add_user(
            email=_EMP, password=_EMP_PW, must_change_password=True
        )
        c = self.login_client(_EMP, _EMP_PW)
        me = c.get("/api/auth/me")
        self.assertEqual(me.status_code, 200)
        self.assertTrue(me.json()["must_change_password"])
        # 业务接口被硬门禁挡住
        r = c.get("/api/products")
        self.assertEqual(r.status_code, 403, r.text)
        self.assertIn("请先修改密码", r.json()["detail"])
        # 改完密码后放行
        r = c.post(
            "/api/auth/password",
            json={"old_password": _EMP_PW, "new_password": "FixedNew2026"},
        )
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(c.get("/api/products").status_code, 200)

    def test_invite_set_password_then_login(self):
        # 直接造一条「待邀请」员工(库里只有 token 哈希,无密码)
        raw = "invite-token-abc123"
        with SessionLocal() as db:
            db.add(
                User(
                    email=_EMP,
                    name="待邀请员工",
                    role="employee",
                    is_active=True,
                    invite_token_hash=hash_token(raw),
                    invite_token_expires_at=datetime.now(timezone.utc)
                    + timedelta(hours=1),
                )
            )
            db.commit()
        c = TestClient(app)
        r = c.post(
            "/api/auth/set-password",
            json={"token": raw, "new_password": _EMP_PW},
        )
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["email"], _EMP)
        # 设完密码即登录:同 client 能访问业务
        self.assertEqual(c.get("/api/products").status_code, 200)
        # token 一次性,复用即 400
        r2 = c.post(
            "/api/auth/set-password",
            json={"token": raw, "new_password": "Another2026"},
        )
        self.assertEqual(r2.status_code, 400, r2.text)

    def test_invite_token_expired(self):
        raw = "invite-token-expired"
        with SessionLocal() as db:
            db.add(
                User(
                    email=_EMP,
                    name="过期邀请",
                    role="employee",
                    is_active=True,
                    invite_token_hash=hash_token(raw),
                    invite_token_expires_at=datetime.now(timezone.utc)
                    - timedelta(hours=1),
                )
            )
            db.commit()
        r = TestClient(app).post(
            "/api/auth/set-password",
            json={"token": raw, "new_password": _EMP_PW},
        )
        self.assertEqual(r.status_code, 400, r.text)
        self.assertIn("无效或已过期", r.json()["detail"])

    def test_invited_user_cannot_login_before_set_password(self):
        raw = "invite-token-nologin"
        with SessionLocal() as db:
            db.add(
                User(
                    email=_EMP,
                    name="未设密",
                    role="employee",
                    is_active=True,
                    invite_token_hash=hash_token(raw),
                    invite_token_expires_at=datetime.now(timezone.utc)
                    + timedelta(hours=1),
                )
            )
            db.commit()
        r = self.client.post(
            "/api/auth/login", json={"email": _EMP, "password": "Whatever2026"}
        )
        self.assertEqual(r.status_code, 401, r.text)

    def test_short_password_rejected(self):
        raw = "invite-token-short"
        with SessionLocal() as db:
            db.add(
                User(
                    email=_EMP,
                    name="短密码",
                    role="employee",
                    is_active=True,
                    invite_token_hash=hash_token(raw),
                    invite_token_expires_at=datetime.now(timezone.utc)
                    + timedelta(hours=1),
                )
            )
            db.commit()
        r = TestClient(app).post(
            "/api/auth/set-password", json={"token": raw, "new_password": "short"}
        )
        self.assertEqual(r.status_code, 422, r.text)


class BootstrapTestCase(unittest.TestCase):
    """首个管理员引导:lifespan 只在 `with TestClient(app)` 里执行。"""

    def setUp(self) -> None:
        Base.metadata.drop_all(bind=engine)
        Base.metadata.create_all(bind=engine)

    def test_bootstrap_admin_created_once(self):
        os.environ["ADMIN_EMAIL"] = "root@test.local"
        os.environ["ADMIN_PASSWORD"] = "RootPass2026"
        try:
            # lifespan 执行 → 空库建首个管理员
            with TestClient(app):
                pass
            with SessionLocal() as db:
                users = db.scalars(select(User)).all()
                self.assertEqual(len(users), 1)
                self.assertEqual(users[0].role, "admin")
                self.assertEqual(users[0].email, "root@test.local")
            # 幂等:再启动一次不重复建
            with TestClient(app):
                pass
            with SessionLocal() as db:
                self.assertEqual(db.scalars(select(User)).all().__len__(), 1)
            # 该管理员能登录
            c = TestClient(app)
            r = c.post(
                "/api/auth/login",
                json={"email": "root@test.local", "password": "RootPass2026"},
            )
            self.assertEqual(r.status_code, 200, r.text)
        finally:
            os.environ.pop("ADMIN_EMAIL", None)
            os.environ.pop("ADMIN_PASSWORD", None)

    def test_bootstrap_skipped_without_env(self):
        os.environ.pop("ADMIN_EMAIL", None)
        os.environ.pop("ADMIN_PASSWORD", None)
        with TestClient(app):
            pass
        with SessionLocal() as db:
            self.assertEqual(db.scalars(select(User)).all().__len__(), 0)
