"""凭证加密存储：Fernet + PBKDF2。

凭证包括：
- account : Flyme 账号（仅显示用）
- token   : 从浏览器复制粘贴的 Flyme token
- cookies : 附加 Cookie（可选）

主密钥派生自机器节点名 + 随机 salt 文件（位于 ~/.flyme-exporter/salt.bin）。
"""
from __future__ import annotations

import base64
import json
import platform
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from .config import CREDENTIALS_FILE, DATA_HOME, SALT_FILE, ensure_dirs


@dataclass
class Credentials:
    """登录态凭证。"""

    account: str
    token: str
    cookies: dict[str, str] | None = None

    def to_dict(self) -> dict:
        return {
            "account": self.account,
            "token": self.token,
            "cookies": self.cookies or {},
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Credentials":
        return cls(
            account=data.get("account", ""),
            token=data.get("token", ""),
            cookies=data.get("cookies", {}),
        )


class CredentialStore:
    """本地加密凭证存储。"""

    def __init__(self) -> None:
        ensure_dirs()

    def _load_or_create_salt(self) -> bytes:
        if SALT_FILE.exists():
            return SALT_FILE.read_bytes()
        salt_src = f"{platform.node()}-{uuid.uuid4()}".encode("utf-8")
        SALT_FILE.write_bytes(salt_src)
        return salt_src

    def _derive_key(self) -> bytes:
        salt = self._load_or_create_salt()
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=480_000,
        )
        return base64.urlsafe_b64encode(kdf.derive(b"flyme-exporter-v1"))

    def save(self, creds: Credentials) -> None:
        ensure_dirs()
        f = Fernet(self._derive_key())
        token = f.encrypt(json.dumps(creds.to_dict()).encode("utf-8"))
        tmp = CREDENTIALS_FILE.with_suffix(".enc.tmp")
        tmp.write_bytes(token)
        tmp.replace(CREDENTIALS_FILE)

    def load(self) -> Optional[Credentials]:
        if not CREDENTIALS_FILE.exists():
            return None
        try:
            f = Fernet(self._derive_key())
            data = json.loads(f.decrypt(CREDENTIALS_FILE.read_bytes()))
            return Credentials.from_dict(data)
        except (InvalidToken, json.JSONDecodeError):
            return None

    def clear(self) -> None:
        if CREDENTIALS_FILE.exists():
            CREDENTIALS_FILE.unlink()