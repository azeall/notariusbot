import base64
import hashlib
import hmac
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, VerificationError
from cryptography.fernet import Fernet

from app.config import get_settings

_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError):
        return False


def generate_token() -> str:
    """Секрет одноразовой ссылки. Показывается один раз, в базу не пишется."""
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    """В базе лежит только это. Токен из ссылки сверяем по хешу."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def tokens_equal(a: str, b: str) -> bool:
    return hmac.compare_digest(a, b)


def _fernet() -> Fernet:
    """Ключ шифрования документов.

    Настройка может содержать как готовый ключ Fernet, так и произвольную строку —
    во втором случае разворачиваем её в 32 байта. Это удобно для разработки,
    но в продакшне ключ обязан быть случайным и лежать вне репозитория.
    """
    raw = get_settings().document_encryption_key
    try:
        key = raw.encode("utf-8")
        Fernet(key)
    except (ValueError, TypeError):
        digest = hashlib.sha256(raw.encode("utf-8")).digest()
        key = base64.urlsafe_b64encode(digest)
    return Fernet(key)


def encrypt_bytes(data: bytes) -> bytes:
    return _fernet().encrypt(data)


def decrypt_bytes(data: bytes) -> bytes:
    return _fernet().decrypt(data)
