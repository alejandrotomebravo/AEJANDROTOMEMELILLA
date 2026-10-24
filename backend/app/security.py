"""Contraseñas, sesiones, URL firmadas y seudonimización."""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time

from .config import settings

_PBKDF2_ITER = 310_000


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _PBKDF2_ITER)
    return f"pbkdf2_sha256${_PBKDF2_ITER}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, iters, salt_hex, dk_hex = stored.split("$")
    except ValueError:
        return False
    if algo != "pbkdf2_sha256":
        return False
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt_hex), int(iters))
    return hmac.compare_digest(dk.hex(), dk_hex)


def new_token() -> str:
    return secrets.token_urlsafe(32)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def pseudonymize(value: str | None) -> str | None:
    """Hash con clave para IP/agente de usuario: permite auditoría sin guardar el dato."""
    if not value:
        return None
    return hmac.new(settings.secret_key.encode(), value.encode(), hashlib.sha256).hexdigest()[:32]


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def sign_asset(asset_id: str, ttl: int = 3600, purpose: str = "view") -> str:
    exp = int(time.time()) + ttl
    payload = f"{asset_id}.{exp}.{purpose}"
    sig = hmac.new(settings.secret_key.encode(), payload.encode(), hashlib.sha256).digest()
    return f"{_b64(payload.encode())}.{_b64(sig[:20])}"


def verify_asset_signature(token: str) -> tuple[str, str] | None:
    try:
        p, s = token.split(".")
        payload = _unb64(p).decode()
        asset_id, exp, purpose = payload.split(".")
    except Exception:
        return None
    expected = hmac.new(settings.secret_key.encode(), payload.encode(), hashlib.sha256).digest()[:20]
    if not hmac.compare_digest(expected, _unb64(s)):
        return None
    if int(exp) < time.time():
        return None
    return asset_id, purpose


def sha256_file(path, chunk: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()
