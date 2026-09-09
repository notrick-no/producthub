"""密码哈希与一次性 token(bcrypt + 标准库 secrets)。

- 密码用 bcrypt(只哈希前 72 字节 → 密码长度上限见 schemas,一律 ≤72)。
- 会话 token / 邀请 token 都是 `secrets.token_urlsafe` 随机串,**库里只存 sha256**,
  库被拖也不至于直接拿到可用 token。
- 本模块不 import 任何 app 内部模块(避免循环依赖),邮件/会话需要「应用地址」
  也在此读 env。
"""
import hashlib
import os
import secrets

import bcrypt


def app_base_url() -> str:
    """供邮件链接与 cookie Secure 判断的应用地址(去尾部斜杠)。"""
    return os.getenv("APP_BASE_URL", "http://localhost:5173").rstrip("/")


def hash_password(password: str) -> str:
    """bcrypt 哈希(自带随机盐),返回可入库的字符串。"""
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str | None) -> bool:
    """校验明文密码;哈希为空/非法一律 False,不泄露账号是否存在。"""
    if not password_hash:
        return False
    try:
        return bcrypt.checkpw(
            password.encode("utf-8"), password_hash.encode("utf-8")
        )
    except ValueError:
        return False


def hash_token(raw_token: str) -> str:
    """token 明文 → 入库用 sha256 十六进制(64 字符,对应列长度)。"""
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def new_session_token() -> tuple[str, str]:
    """生成会话 token,返回 (明文, sha256);明文只放 HttpOnly cookie。"""
    raw = secrets.token_urlsafe(32)
    return raw, hash_token(raw)


def new_invite_token() -> tuple[str, str]:
    """生成邀请 token(同上:明文给邮件链接,库存哈希)。"""
    raw = secrets.token_urlsafe(32)
    return raw, hash_token(raw)


# 临时密码用的字母表:去掉易混字符(0/O、1/l/I),仅小写字母 + 数字
_TEMP_LETTERS = "abcdefghjkmnpqrstuvwxyz"
_TEMP_ALPHABET = _TEMP_LETTERS + "ABCDEFGHJKMNPQRSTUVWXYZ23456789"


def make_temp_password() -> str:
    """生成 12 位临时密码,保证至少含一个字母和一个数字(避开易混字符)。"""
    parts = [
        secrets.choice(_TEMP_LETTERS),
        secrets.choice("23456789"),
        *(secrets.choice(_TEMP_ALPHABET) for _ in range(10)),
    ]
    secrets.SystemRandom().shuffle(parts)
    return "".join(parts)
