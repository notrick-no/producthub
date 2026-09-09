"""mailer._send 传输层选择:587+STARTTLS / 465 隐式 TLS / SMTP_STARTTLS="0" 明文 / Resend API。"""
import io
import json
import os
import unittest
from unittest import mock

from app import mailer


class _FakeSMTP:
    instances: list = []

    def __init__(self, host, port, timeout=15, *args, **kwargs):
        self.host = host
        self.port = port
        self.ssl = False
        self.started = False
        self.logged = False
        self.sent = False
        type(self).instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def starttls(self):
        self.started = True

    def login(self, user, pwd):
        self.logged = True

    def send_message(self, msg):
        self.sent = True


class _FakeSMTP_SSL(_FakeSMTP):
    def __init__(self, host, port, timeout=15):
        super().__init__(host, port, timeout=timeout)
        self.ssl = True


class MailerTransportTests(unittest.TestCase):
    """_send 按 SMTP_PORT / SMTP_STARTTLS 选择正确的传输方式。"""

    def setUp(self):
        _FakeSMTP.instances = []

    def _send(self, port, starttls=None):
        env = {
            "SMTP_HOST": "smtp.example.test",
            "SMTP_FROM": "a@example.test",
            "SMTP_USERNAME": "user",
            "SMTP_PASSWORD": "pass",
            "SMTP_PORT": str(port),
        }
        if starttls is not None:
            env["SMTP_STARTTLS"] = starttls
        with (
            mock.patch.object(mailer.smtplib, "SMTP", _FakeSMTP),
            mock.patch.object(mailer.smtplib, "SMTP_SSL", _FakeSMTP_SSL),
            mock.patch.dict(os.environ, env, clear=False),
        ):
            mailer._send("to@example.test", "subject", "<p>hi</p>")
        return _FakeSMTP.instances

    def test_587_default_starttls(self):
        inst = self._send(587)
        self.assertEqual(len(inst), 1)
        smtp = inst[0]
        self.assertFalse(smtp.ssl, "587 应走普通 SMTP,不应 SMTP_SSL")
        self.assertTrue(smtp.started, "587 默认应 STARTTLS")
        self.assertTrue(smtp.logged)
        self.assertTrue(smtp.sent)

    def test_587_starttls_off(self):
        smtp = self._send(587, starttls="0")[0]
        self.assertFalse(smtp.started, 'SMTP_STARTTLS="0" 应跳过 STARTTLS')

    def test_465_implicit_tls(self):
        inst = self._send(465, starttls="1")
        self.assertEqual(len(inst), 1)
        smtp = inst[0]
        self.assertTrue(smtp.ssl, "465 应走 SMTP_SSL(隐式 TLS)")
        self.assertFalse(smtp.started, "465 不需要 STARTTLS")
        self.assertTrue(smtp.logged)
        self.assertTrue(smtp.sent)

    def test_mail_not_configured(self):
        # 显式置空 SMTP_HOST/SMTP_FROM(防止继承 .env 里的残留值)
        env = {
            "SMTP_HOST": "",
            "SMTP_FROM": "",
            "SMTP_PORT": "587",
            "SMTP_USERNAME": "user",
            "SMTP_PASSWORD": "pass",
        }
        with mock.patch.dict(os.environ, env, clear=False):
            with self.assertRaises(mailer.MailNotConfigured):
                mailer._send("to@example.test", "subject", "<p>hi</p>")


    def test_resend_api_used_when_key_set(self):
        """有 RESEND_API_KEY → 走 Resend HTTP API,不碰 smtplib。"""
        calls = {}

        def fake_urlopen(req, timeout=None):
            calls["url"] = req.full_url
            calls["method"] = req.get_method()
            calls["auth"] = req.get_header("Authorization")
            # Request.headers 的 key 已归一(如 "User-agent"),get_header 精确名查不到,直接遍历
            calls["ua"] = next(
                (v for k, v in req.headers.items() if k.lower() == "user-agent"), None
            )
            calls["body"] = json.loads(req.data.decode("utf-8"))

            class _Resp:
                def __enter__(self):
                    return self

                def __exit__(self, *exc):
                    return False

                def read(self):
                    return b'{"id":"ok"}'

            return _Resp()

        def _boom(*args, **kwargs):
            raise AssertionError("配了 API key 不应走 smtplib")

        env = {
            "SMTP_FROM": "noreply@example.test",
            "SMTP_FROM_NAME": "潮汐基石producthub",
            "RESEND_API_KEY": "re_test",
        }
        with (
            mock.patch.object(mailer.urllib.request, "urlopen", fake_urlopen),
            mock.patch.object(mailer.smtplib, "SMTP", _boom),
            mock.patch.object(mailer.smtplib, "SMTP_SSL", _boom),
            mock.patch.dict(os.environ, env, clear=False),
        ):
            mailer._send("lin@example.test", "测试主题", "<p>hi</p>")

        self.assertEqual(calls["url"], "https://api.resend.com/emails")
        self.assertEqual(calls["method"], "POST")
        self.assertEqual(calls["auth"], "Bearer re_test")
        self.assertIsNotNone(calls["ua"], "必须带自定义 UA,否则 Cloudflare 拦 403 1010")
        self.assertNotIn("Python-urllib", calls["ua"])
        self.assertEqual(calls["body"]["to"], ["lin@example.test"])
        self.assertEqual(calls["body"]["subject"], "测试主题")
        self.assertIn("noreply@example.test", calls["body"]["from"])

    def test_resend_api_http_error_raises_mailsend(self):
        def fake_urlopen(req, timeout=None):
            raise mailer.urllib.error.HTTPError(
                "https://api.resend.com/emails", 401, "Unauthorized", {}, io.BytesIO(b'{"message":"no"}')
            )

        env = {"SMTP_FROM": "a@b.test", "RESEND_API_KEY": "re_x"}
        with (
            mock.patch.object(mailer.urllib.request, "urlopen", fake_urlopen),
            mock.patch.dict(os.environ, env, clear=False),
        ):
            with self.assertRaises(mailer.MailSendError):
                mailer._send("to@x.test", "s", "<p>x</p>")

    def test_configured_via_api_key_without_smtp_host(self):
        env = {"SMTP_FROM": "a@b.test", "RESEND_API_KEY": "re_x", "SMTP_HOST": ""}
        with mock.patch.dict(os.environ, env, clear=False):
            self.assertTrue(mailer.is_mail_configured())


if __name__ == "__main__":
    unittest.main()
