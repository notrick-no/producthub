"""登录审计(第五版 5A):login_events 的写入、读取与保留期清理。

这里最要紧的一条是**失败也要落库** —— 它有一个不出声的失败模式:
get_db 只 close 不 commit,所以「add 一条然后 raise 401」会让失败记录被静默丢弃,
表里只剩成功记录,而任何地方都不会报错。所以有专门的用例盯着它。
"""
import unittest
from datetime import timedelta

from starlette.datastructures import Address

from tests.base import ApiTestCase, SessionLocal
from app import models
from app.deps import now_utc
from app.request_ip import client_ip


class FakeRequest:
    """只带 client_ip 需要的两样东西(headers + client)。

    真的 Request 也能构造,但那要拼 ASGI scope,这里要测的是解析规则本身。
    client 用 starlette 自己的 Address,而不是随手一个 tuple ——
    假的形状和真的不一样的话,测的就是假的那个。
    """

    def __init__(self, headers: dict | None = None, client=Address("10.0.0.1", 1234)):
        self.headers = headers or {}
        self.client = client


class ClientIpTest(unittest.TestCase):
    """纯解析规则,不碰数据库。"""

    def test_no_forwarded_header_falls_back_to_peer(self):
        """本机 pnpm dev 直连 8000 没有代理,也就没有 XFF,必须走回退。"""
        self.assertEqual(client_ip(FakeRequest()), "10.0.0.1")

    def test_no_client_at_all(self):
        """TestClient 之外的一些调用路径可能没有 client,不能 AttributeError。"""
        self.assertIsNone(client_ip(FakeRequest(client=None)))

    def test_single_hop(self):
        req = FakeRequest({"x-forwarded-for": "203.0.113.7"})
        self.assertEqual(client_ip(req), "203.0.113.7")

    def test_multi_hop_takes_rightmost(self):
        """左边那些客户端能随手伪造,最右边那个由最后一跳追加。"""
        req = FakeRequest({"x-forwarded-for": "1.2.3.4, 10.0.0.9, 203.0.113.7"})
        self.assertEqual(client_ip(req), "203.0.113.7")

    def test_spoofed_left_entry_loses(self):
        """客户端自己塞的 XFF 会被代理追加在左边 —— 它赢不了。"""
        req = FakeRequest({"x-forwarded-for": "9.9.9.9, 203.0.113.7"})
        self.assertEqual(client_ip(req), "203.0.113.7")

    def test_strips_port_from_ipv4(self):
        req = FakeRequest({"x-forwarded-for": "203.0.113.7:5566"})
        self.assertEqual(client_ip(req), "203.0.113.7")

    def test_bracketed_ipv6_keeps_address_only(self):
        req = FakeRequest({"x-forwarded-for": "[2001:db8::1]:443"})
        self.assertEqual(client_ip(req), "2001:db8::1")

    def test_bare_ipv6_is_not_split(self):
        """裸 IPv6 有多个冒号,按冒号切会把地址切碎。"""
        req = FakeRequest({"x-forwarded-for": "2001:db8::1"})
        self.assertEqual(client_ip(req), "2001:db8::1")

    def test_trailing_empty_entry_ignored(self):
        req = FakeRequest({"x-forwarded-for": "203.0.113.7, "})
        self.assertEqual(client_ip(req), "203.0.113.7")

    def test_overlong_value_is_truncated_to_column_width(self):
        """列宽 45;不截断的话 Postgres 会抛 StringDataRightTruncation。"""
        req = FakeRequest({"x-forwarded-for": "x" * 500})
        self.assertEqual(len(client_ip(req)), 45)


class LoginEventWriteTest(ApiTestCase):
    def _rows(self) -> list[models.LoginEvent]:
        with SessionLocal() as db:
            return list(
                db.query(models.LoginEvent).order_by(models.LoginEvent.id).all()
            )

    def test_success_is_recorded(self):
        """setUp 里 self.client 已登录过 admin,应有一条成功记录。"""
        rows = [r for r in self._rows() if r.succeeded]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].email, "admin@test.local")
        self.assertIsNotNone(rows[0].user_id)

    def test_wrong_password_is_recorded_not_silently_dropped(self):
        """这条盯的是「add 完就 raise」那个静默丢弃陷阱。"""
        before = len(self._rows())
        r = self.client.post(
            "/api/auth/login",
            json={"email": "admin@test.local", "password": "WrongPassword123"},
        )
        self.assertEqual(r.status_code, 401)
        rows = self._rows()
        self.assertEqual(len(rows), before + 1, "失败尝试没有落库")
        self.assertFalse(rows[-1].succeeded)
        # 邮箱存在时 user_id 要填上 —— 审计正是要看出「谁在试」
        self.assertIsNotNone(rows[-1].user_id)

    def test_unknown_email_is_recorded_with_null_user(self):
        r = self.client.post(
            "/api/auth/login",
            json={"email": "nobody@test.local", "password": "WhateverPass123"},
        )
        self.assertEqual(r.status_code, 401)
        row = self._rows()[-1]
        self.assertFalse(row.succeeded)
        self.assertIsNone(row.user_id)
        self.assertEqual(row.email, "nobody@test.local")

    def test_disabled_account_is_recorded(self):
        self._add_user(
            email="gone@test.local",
            password="SomePassword123",
            is_active=False,
        )
        r = self.client.post(
            "/api/auth/login",
            json={"email": "gone@test.local", "password": "SomePassword123"},
        )
        self.assertEqual(r.status_code, 401)
        row = self._rows()[-1]
        self.assertFalse(row.succeeded)
        self.assertIsNotNone(row.user_id)

    def test_email_is_normalized_in_record(self):
        """填大写、带空格也要记成规范形式,否则按邮箱查审计会漏。"""
        self.client.post(
            "/api/auth/login",
            json={"email": "  ADMIN@TEST.LOCAL  ", "password": "WrongPassword123"},
        )
        self.assertEqual(self._rows()[-1].email, "admin@test.local")

    def test_overlong_user_agent_does_not_500(self):
        """公开端点:陌生人塞一个超长 UA 不能把我们打成 500。"""
        r = self.client.post(
            "/api/auth/login",
            json={"email": "admin@test.local", "password": "WrongPassword123"},
            headers={"User-Agent": "A" * 5000},
        )
        self.assertEqual(r.status_code, 401)
        self.assertLessEqual(len(self._rows()[-1].user_agent or ""), 255)

    def test_ip_and_agent_are_captured(self):
        self.client.post(
            "/api/auth/login",
            json={"email": "admin@test.local", "password": "WrongPassword123"},
            headers={
                "X-Forwarded-For": "1.2.3.4, 203.0.113.7",
                "User-Agent": "pytest-agent/1.0",
            },
        )
        row = self._rows()[-1]
        self.assertEqual(row.ip, "203.0.113.7")
        self.assertEqual(row.user_agent, "pytest-agent/1.0")

    def test_deleting_user_keeps_the_audit_row(self):
        """账号没了,审计记录不能跟着消失(所以是 SET NULL 而不是 CASCADE)。"""
        uid = self._add_user(email="temp@test.local", password="SomePassword123")
        self.client.post(
            "/api/auth/login",
            json={"email": "temp@test.local", "password": "SomePassword123"},
        )
        with SessionLocal() as db:
            db.delete(db.get(models.User, uid))
            db.commit()
        row = self._rows()[-1]
        self.assertIsNone(row.user_id)
        self.assertEqual(row.email, "temp@test.local")
        self.assertTrue(row.succeeded)

    def test_prune_drops_rows_past_retention(self):
        """成功登录时顺手清理。手工把一条记录改成 200 天前再登一次。"""
        self.client.post(
            "/api/auth/login",
            json={"email": "admin@test.local", "password": "WrongPassword123"},
        )
        with SessionLocal() as db:
            old = db.query(models.LoginEvent).order_by(models.LoginEvent.id).first()
            old.created_at = now_utc() - timedelta(days=200)
            db.commit()
            old_id = old.id

        self.client.post(
            "/api/auth/login",
            json={"email": "admin@test.local", "password": "AdminTest2026"},
        )
        with SessionLocal() as db:
            self.assertIsNone(db.get(models.LoginEvent, old_id))

    def test_failed_login_does_not_prune(self):
        """清理挂在成功路径上;失败路径别顺手删东西。"""
        self.client.post(
            "/api/auth/login",
            json={"email": "admin@test.local", "password": "WrongPassword123"},
        )
        with SessionLocal() as db:
            old = db.query(models.LoginEvent).order_by(models.LoginEvent.id).first()
            old.created_at = now_utc() - timedelta(days=200)
            db.commit()
            old_id = old.id

        self.client.post(
            "/api/auth/login",
            json={"email": "admin@test.local", "password": "WrongPassword123"},
        )
        with SessionLocal() as db:
            self.assertIsNotNone(db.get(models.LoginEvent, old_id))


class UserListLastLoginTest(ApiTestCase):
    def _users_by_email(self) -> dict:
        r = self.client.get("/api/users")
        self.assertEqual(r.status_code, 200, r.text)
        return {u["email"]: u for u in r.json()}

    def test_last_login_is_the_most_recent_success(self):
        admin = self._users_by_email()["admin@test.local"]
        self.assertIsNotNone(admin["last_login_at"])
        self.assertIsNotNone(admin["last_login_ip"])

    def test_last_login_ignores_failed_attempts(self):
        """登录成功后又手滑输错一次,那一列不能变成失败的时间和 IP。"""
        before = self._users_by_email()["admin@test.local"]
        self.client.post(
            "/api/auth/login",
            json={"email": "admin@test.local", "password": "WrongPassword123"},
            headers={"X-Forwarded-For": "198.51.100.9"},
        )
        after = self._users_by_email()["admin@test.local"]
        self.assertEqual(after["last_login_at"], before["last_login_at"])
        self.assertEqual(after["last_login_ip"], before["last_login_ip"])

    def test_last_login_updates_after_a_new_success(self):
        before = self._users_by_email()["admin@test.local"]["last_login_at"]
        self.client.post(
            "/api/auth/login",
            json={"email": "admin@test.local", "password": "AdminTest2026"},
        )
        after = self._users_by_email()["admin@test.local"]["last_login_at"]
        self.assertGreaterEqual(after, before)

    def test_user_who_never_logged_in_has_nulls(self):
        self._add_user(email="newbie@test.local", password="SomePassword123")
        row = self._users_by_email()["newbie@test.local"]
        self.assertIsNone(row["last_login_at"])
        self.assertIsNone(row["last_login_ip"])

    def test_each_user_gets_their_own_last_login(self):
        """DISTINCT ON 按 user_id 分组 —— 不能所有人都拿到同一个人的记录。"""
        self._add_user(email="other@test.local", password="OtherPass2026")
        self.login_client("other@test.local", "OtherPass2026")
        users = self._users_by_email()
        self.assertIsNotNone(users["other@test.local"]["last_login_at"])
        self.assertIsNotNone(users["admin@test.local"]["last_login_at"])

    def test_me_endpoint_does_not_carry_last_login(self):
        """/auth/me 不查这个,留空即可 —— 别为了填满字段给它加一次查询。"""
        r = self.client.get("/api/auth/me")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIsNone(r.json()["last_login_at"])


if __name__ == "__main__":
    unittest.main()
