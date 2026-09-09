"""mailer._send 传输层选择:587+STARTTLS / 465 隐式 TLS / SMTP_STARTTLS="0" 明文。"""
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


if __name__ == "__main__":
    unittest.main()
