"""真实邮件发送(邀请设密与重置密码)。

两条发送路径,都从环境变量读取(**函数内 os.getenv**,便于测试中途覆盖;.env 加载复用 app/db.py):

1. Resend HTTP API(优先):设了 `RESEND_API_KEY` 就走 https://api.resend.com/emails(443),
   绕开云平台常封的 SMTP 出站端口;key 与 SMTP_PASSWORD 是同一把 `re_…`。
2. SMTP(stdlib smtplib):`SMTP_HOST / SMTP_PORT(默认 587;设 465 = SMTPS 隐式 TLS)` +
   `SMTP_USERNAME / SMTP_PASSWORD / SMTP_FROM`;`SMTP_STARTTLS` 仅 587 等普通端口生效,
   默认开,设 "0" 关闭(465 自动走 SMTP_SSL)。

公共项:`SMTP_FROM`(发件地址,必填)、`SMTP_FROM_NAME`(显示名,可选)、
`APP_BASE_URL`(邮件里邀请链接前缀,默认 http://localhost:5173)。

SMTP_HOST+SMTP_FROM,或 RESEND_API_KEY+SMTP_FROM 都缺 → MailNotConfigured;
发送失败 → MailSendError。上层统一 503 拒绝创建/重置,不留「库里建了号但没发信」的半截状态。
"""
import html
import json
import logging
import os
import smtplib
import urllib.error
import urllib.request
from email.message import EmailMessage
from email.utils import formataddr

from .security import app_base_url

log = logging.getLogger("producthub.mail")


class MailNotConfigured(Exception):
    """发信未配置(503)。"""

    def __init__(self):
        super().__init__(
            "邮件未配置,无法发送:请在环境变量配置 SMTP_HOST+SMTP_FROM,或 RESEND_API_KEY+SMTP_FROM"
        )


class MailSendError(Exception):
    """SMTP 发送失败(503),detail 提示检查配置。"""

    def __init__(self):
        super().__init__("邮件发送失败,请稍后重试或检查 SMTP 配置")


def is_mail_configured() -> bool:
    """配齐「SMTP_HOST + SMTP_FROM」或「RESEND_API_KEY + SMTP_FROM」任一即视为已配置。"""
    return bool(os.getenv("SMTP_FROM")) and bool(
        os.getenv("SMTP_HOST") or os.getenv("RESEND_API_KEY")
    )


TEXT_FALLBACK = "本邮件包含 HTML 内容,请用支持 HTML 的邮件客户端查看。"


def _send(to_email: str, subject: str, html_body: str) -> None:
    """真实发信:配了 RESEND_API_KEY 走 Resend HTTP API(443),否则走 smtplib。"""
    if not is_mail_configured():
        raise MailNotConfigured()
    if os.getenv("RESEND_API_KEY"):
        _send_resend_api(to_email, subject, html_body)
        return
    host = os.getenv("SMTP_HOST", "")
    port = int(os.getenv("SMTP_PORT", "587"))
    username = os.getenv("SMTP_USERNAME")
    password = os.getenv("SMTP_PASSWORD")
    sender = os.getenv("SMTP_FROM", "")
    sender_name = os.getenv("SMTP_FROM_NAME", "producthub")

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = formataddr((sender_name, sender))
    msg["To"] = to_email
    msg.set_content(TEXT_FALLBACK)
    msg.add_alternative(html_body, subtype="html")

    try:
        if port == 465:  # 端口 465 = SMTPS 隐式 TLS,不需要 STARTTLS
            smtp = smtplib.SMTP_SSL(host, port, timeout=15)
        else:  # 587 等普通端口:默认 STARTTLS,可设 SMTP_STARTTLS="0" 关闭
            smtp = smtplib.SMTP(host, port, timeout=15)
            if os.getenv("SMTP_STARTTLS", "1") == "1":
                smtp.starttls()
        with smtp:
            if username and password:
                smtp.login(username, password)
            smtp.send_message(msg)
    except (smtplib.SMTPException, OSError) as exc:
        log.warning("SMTP 发送失败: %s", exc)
        raise MailSendError() from exc


def _send_resend_api(to_email: str, subject: str, html_body: str) -> None:
    """Resend HTTP API 发信(https://api.resend.com/emails,走 443)。"""
    sender = os.getenv("SMTP_FROM", "")
    sender_name = os.getenv("SMTP_FROM_NAME", "producthub")
    payload = {
        "from": f"{sender_name} <{sender}>",
        "to": [to_email],
        "subject": subject,
        "html": html_body,
        "text": TEXT_FALLBACK,
    }
    req = urllib.request.Request(
        "https://api.resend.com/emails",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {os.getenv('RESEND_API_KEY', '')}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=25) as resp:
            resp.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:500]
        log.warning("Resend API 发送失败: %s %s", exc.code, detail)
        raise MailSendError() from exc
    except OSError as exc:
        log.warning("Resend API 发送失败: %s", exc)
        raise MailSendError() from exc


def _display(name: str) -> str:
    return html.escape(name or "用户")


def send_user_invite(email: str, name: str, raw_token: str) -> None:
    """员工账号创建后发邀请:点链接自设初始密码(链接 72 小时内一次性有效)。"""
    link = f"{app_base_url()}/set-password?token={raw_token}"
    _send(
        email,
        "producthub · 邀请你设置登录密码",
        f"""
        <div style="font-family:sans-serif;line-height:1.7">
          <p>{_display(name)} 你好,</p>
          <p>管理员在 producthub 为你创建了账号(<strong>{html.escape(email)}</strong>)。</p>
          <p>请点击下面的链接设置你的登录密码(72 小时内有效、仅可使用一次):</p>
          <p><a href="{html.escape(link, quote=True)}" style="color:#7e14ff">设置密码</a></p>
          <p style="color:#888;font-size:12px">链接打不开可复制:{html.escape(link, quote=True)}</p>
        </div>
        """,
    )


def send_reset_password(email: str, name: str, temp_password: str) -> None:
    """管理员重置密码:发临时密码,并提示首次登录须修改(系统已强制)。"""
    _send(
        email,
        "producthub · 账号密码已重置",
        f"""
        <div style="font-family:sans-serif;line-height:1.7">
          <p>{_display(name)} 你好,</p>
          <p>管理员已重置你在 producthub 的登录密码。</p>
          <p>临时密码:<strong style="font-size:18px;letter-spacing:1px">{html.escape(temp_password)}</strong></p>
          <p>请用它登录,并在首次登录后<strong>立即修改密码</strong>(修改前无法使用产品功能)。</p>
          <p style="color:#888;font-size:12px">若非本人操作,请尽快联系管理员。</p>
        </div>
        """,
    )
