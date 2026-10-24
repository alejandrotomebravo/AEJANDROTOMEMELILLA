from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, Depends, Header
from pydantic import BaseModel

from .. import db, storage
from ..config import settings
from ..errors import AppError, Conflict
from ..security import hash_password, new_token, token_hash, verify_password
from .deps import Unauthorized, current_user

router = APIRouter(prefix="/api/auth", tags=["autenticación"])


class Credentials(BaseModel):
    email: str
    password: str


def _session(user_id: str) -> str:
    token = new_token()
    db.execute("INSERT INTO sessions(token_hash,user_id,created_at,expires_at) VALUES (?,?,?,?)",
               (token_hash(token), user_id, db.now(), db.now() + settings.session_days * 86400))
    return token


@router.post("/register")
def register(body: Credentials) -> dict[str, Any]:
    email = body.email.strip().lower()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        raise AppError("Correo electrónico no válido.")
    if len(body.password) < 10:
        raise AppError("La contraseña debe tener al menos 10 caracteres.")
    if db.one("SELECT id FROM users WHERE email=?", (email,)):
        raise Conflict("Ya existe una cuenta con ese correo.")
    uid = db.new_id()
    db.execute("INSERT INTO users(id,email,pw_hash,created_at) VALUES (?,?,?,?)",
               (uid, email, hash_password(body.password), db.now()))
    db.audit("registro", uid)
    return {"token": _session(uid), "user": {"id": uid, "email": email}}


@router.post("/login")
def login(body: Credentials) -> dict[str, Any]:
    u = db.one("SELECT * FROM users WHERE email=?", (body.email.strip().lower(),))
    if not u or not verify_password(body.password, u["pw_hash"]):
        raise Unauthorized("Correo o contraseña incorrectos.")
    return {"token": _session(u["id"]), "user": {"id": u["id"], "email": u["email"]}}


@router.post("/logout")
def logout(authorization: str | None = Header(default=None)) -> dict[str, bool]:
    if authorization and authorization.lower().startswith("bearer "):
        db.execute("DELETE FROM sessions WHERE token_hash=?", (token_hash(authorization.split(" ", 1)[1]),))
    return {"ok": True}


@router.get("/me")
def me(user=Depends(current_user)) -> dict[str, Any]:
    return {"id": user["id"], "email": user["email"]}


@router.delete("/account")
def delete_account(user=Depends(current_user)) -> dict[str, bool]:
    """Derecho de supresión: borra la cuenta, proyectos, identidades y archivos."""
    storage.delete_user_files(user["id"])
    db.execute("DELETE FROM jobs WHERE user_id=?", (user["id"],))
    db.execute("DELETE FROM usage WHERE user_id=?", (user["id"],))
    db.execute("DELETE FROM users WHERE id=?", (user["id"],))
    db.execute("UPDATE audit SET detail='{}' WHERE user_id=?", (user["id"],))
    return {"ok": True}
