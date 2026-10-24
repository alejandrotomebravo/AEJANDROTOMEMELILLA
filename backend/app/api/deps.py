from __future__ import annotations

from typing import Any

from fastapi import Header, Request

from .. import db
from ..errors import AppError
from ..security import token_hash


class Unauthorized(AppError):
    status = 401
    code = "no_autenticado"


def current_user(authorization: str | None = Header(default=None)) -> dict[str, Any]:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise Unauthorized("Inicie sesión para continuar.")
    th = token_hash(authorization.split(" ", 1)[1].strip())
    row = db.one("""SELECT u.id, u.email FROM sessions s JOIN users u ON u.id=s.user_id
                    WHERE s.token_hash=? AND s.expires_at>?""", (th, db.now()))
    if not row:
        raise Unauthorized("La sesión ha caducado. Vuelva a iniciar sesión.")
    return row


def client_ip(request: Request) -> str | None:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else None
