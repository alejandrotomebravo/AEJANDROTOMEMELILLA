"""Identidades autorizadas y flujo de consentimiento verificable.

* «Yo mismo»: autodeclaración del titular de la cuenta (verificación débil:
  se registra la declaración).
* Tercero: se genera un enlace personal y caducable que la persona abre en
  su propio dispositivo. Allí lee el texto exacto, escribe su nombre y acepta.
  Se guarda el hash del texto aceptado, la fecha y la IP/agente seudonimizados.
  La persona puede retirar el consentimiento en cualquier momento con el
  mismo enlace.
* Menores: bloqueado salvo ALLOW_MINORS=true y, aun así, sólo con
  consentimiento del representante legal mediante enlace.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import cv2
from fastapi import APIRouter, Depends, File, Request, UploadFile
from pydantic import BaseModel

from .. import db, policy, services, storage
from ..ai import face_detect, quality
from ..config import settings
from ..errors import AppError, ConsentError, NotFound
from ..security import new_token, pseudonymize, token_hash
from .deps import client_ip, current_user

router = APIRouter(prefix="/api/identities", tags=["identidades"])
public = APIRouter(prefix="/api/consent", tags=["consentimiento"])

SCOPES = {"imagen", "voz"}


class NewIdentity(BaseModel):
    display_name: str
    kind: str  # self | third_party
    is_minor: bool = False
    scopes: list[str] = ["imagen"]


class ConsentRequest(BaseModel):
    purpose: str
    scopes: list[str] = ["imagen"]
    guardian: bool = False


def _out(i: dict[str, Any]) -> dict[str, Any]:
    photos = db.all_("SELECT id, meta FROM assets WHERE identity_id=? AND kind='face_photo'", (i["id"],))
    consents = db.all_("""SELECT id,status,method,scopes,purpose,signer_role,created_at,granted_at,revoked_at,expires_at
                          FROM consents WHERE identity_id=? ORDER BY created_at DESC""", (i["id"],))
    for c in consents:
        c["scopes"] = db.jload(c["scopes"], [])
    return {"id": i["id"], "display_name": i["display_name"], "kind": i["kind"], "is_minor": bool(i["is_minor"]),
            "consent_status": i["consent_status"], "scopes": db.jload(i["scopes"], []) if isinstance(i["scopes"], str)
            else i["scopes"], "created_at": i["created_at"], "revoked_at": i["revoked_at"],
            "photos": [{"id": p["id"], **db.jload(p["meta"], {})} for p in photos], "consents": consents}


@router.get("")
def list_identities(user=Depends(current_user)) -> list[dict[str, Any]]:
    return [_out(i) for i in db.all_("SELECT * FROM identities WHERE user_id=? ORDER BY created_at DESC",
                                     (user["id"],))]


@router.post("")
def create_identity(body: NewIdentity, user=Depends(current_user)) -> dict[str, Any]:
    if body.kind not in ("self", "third_party"):
        raise AppError("Tipo de identidad no válido.")
    scopes = [s for s in body.scopes if s in SCOPES]
    if not scopes:
        raise AppError("Indique si autoriza el uso de imagen, de voz o de ambas.")
    if body.is_minor and not settings.allow_minors:
        raise ConsentError("Por protección de menores, este servidor no permite crear identidades de personas "
                           "menores de edad.")
    if body.is_minor and body.kind == "self":
        raise ConsentError("Las cuentas son sólo para mayores de edad.")
    iid = db.new_id()
    db.execute("""INSERT INTO identities(id,user_id,display_name,kind,is_minor,consent_status,scopes,created_at)
                  VALUES (?,?,?,?,?,?,?,?)""",
               (iid, user["id"], body.display_name.strip()[:80] or "Sin nombre", body.kind, int(body.is_minor),
                "pending", db.jdump(scopes), db.now()))
    db.audit("identidad_creada", user["id"], identity=iid, kind=body.kind, minor=body.is_minor)
    return _out(db.one("SELECT * FROM identities WHERE id=?", (iid,)))


@router.post("/{iid}/photos")
def upload_photo(iid: str, file: UploadFile = File(...), user=Depends(current_user)) -> dict[str, Any]:
    ident = services.load_identity(iid, user["id"])
    if ident["consent_status"] == "revoked":
        raise ConsentError("El consentimiento de esta persona fue retirado.")
    ext = Path(file.filename or "foto.jpg").suffix.lower()
    if ext not in {".jpg", ".jpeg", ".png", ".webp", ".heic"}:
        raise AppError("Formato de imagen no admitido (JPG, PNG, WEBP o HEIC).")
    aid, dest = storage.new_path(user["id"], ext, identity_id=iid)
    storage.save_stream(file.file, dest, max_bytes=25 * 1024 * 1024)
    if ext == ".heic":
        jpg = dest.with_suffix(".jpg")
        from ..media import ffmpeg
        try:
            ffmpeg.run([settings.ffmpeg, "-y", "-i", str(dest), str(jpg)])
        finally:
            dest.unlink(missing_ok=True)
        dest = jpg
    img = cv2.imread(str(dest))
    if img is None:
        dest.unlink(missing_ok=True)
        raise AppError("No se pudo leer la imagen.")
    faces = face_detect.FaceDetector().detect(img)
    rep = quality.assess_face_photo(img.shape[1], img.shape[0], len(faces), max((f.h for f in faces), default=0))
    if not rep["ok"]:
        dest.unlink(missing_ok=True)
        raise AppError(" ".join(rep["blocking"]), code="foto_no_valida")
    yaw = faces[0].yaw
    view = "frontal" if yaw is None or abs(yaw) < 0.25 else ("perfil" if faces[0].profile or abs(yaw) > 0.6
                                                              else "tres_cuartos")
    a = storage.register(aid=aid, user_id=user["id"], identity_id=iid, kind="face_photo", path=dest,
                         mime="image/jpeg" if dest.suffix in (".jpg", ".jpeg") else file.content_type,
                         width=img.shape[1], height=img.shape[0],
                         meta={"view": view, "warnings": rep["warnings"], "face_px": faces[0].h})
    return {"id": a["id"], "view": view, "warnings": rep["warnings"]}


@router.delete("/{iid}/photos/{aid}")
def delete_photo(iid: str, aid: str, user=Depends(current_user)) -> dict[str, bool]:
    services.load_identity(iid, user["id"])
    a = storage.get_owned(aid, user["id"])
    if a["identity_id"] != iid:
        raise NotFound("Foto no encontrada.")
    storage.delete_asset(aid)
    return {"ok": True}


class SelfAttest(BaseModel):
    full_name: str
    confirm_adult: bool
    confirm_is_me: bool


@router.post("/{iid}/self-attest")
def self_attest(iid: str, body: SelfAttest, request: Request, user=Depends(current_user)) -> dict[str, Any]:
    ident = services.load_identity(iid, user["id"])
    if ident["kind"] != "self":
        raise AppError("Esta identidad es de un tercero: use el enlace de consentimiento.")
    if not (body.confirm_adult and body.confirm_is_me and body.full_name.strip()):
        raise ConsentError("Debe confirmar que es usted y que es mayor de edad.")
    statement = policy.consent_statement(body.full_name.strip(), ident["scopes"], "uso propio", user["email"])
    _record_consent(ident, "self_attestation", ident["scopes"], "uso propio", statement, body.full_name.strip(),
                    "titular", request)
    db.execute("UPDATE identities SET consent_status='verified_self' WHERE id=?", (iid,))
    db.audit("autodeclaracion_identidad", user["id"], identity=iid)
    return _out(db.one("SELECT * FROM identities WHERE id=?", (iid,)))


def _record_consent(ident, method, scopes, purpose, statement, signer, role, request, token_h=None,
                    status="granted") -> str:
    cid = db.new_id()
    db.execute("""INSERT INTO consents(id,identity_id,token_hash,status,method,scopes,purpose,statement_version,
                  statement_hash,signer_name,signer_role,created_at,granted_at,ip_hash,ua_hash)
                  VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
               (cid, ident["id"], token_h, status, method, db.jdump(scopes), purpose, policy.CONSENT_STATEMENT_VERSION,
                hashlib.sha256(statement.encode()).hexdigest(), signer, role, db.now(),
                db.now() if status == "granted" else None,
                pseudonymize(client_ip(request)), pseudonymize(request.headers.get("user-agent"))))
    return cid


@router.post("/{iid}/consent-request")
def consent_request(iid: str, body: ConsentRequest, user=Depends(current_user)) -> dict[str, Any]:
    ident = services.load_identity(iid, user["id"])
    if ident["kind"] != "third_party":
        raise AppError("Use la autodeclaración para su propia identidad.")
    if ident["is_minor"] and not body.guardian:
        raise ConsentError("Para menores, el consentimiento debe darlo su representante legal.")
    scopes = [s for s in body.scopes if s in SCOPES]
    if not scopes or not body.purpose.strip():
        raise AppError("Indique la finalidad y el alcance (imagen/voz) del consentimiento.")
    token = new_token()
    cid = db.new_id()
    purpose = body.purpose.strip()[:300]
    statement = policy.consent_statement(ident["display_name"], scopes, purpose, user["email"], body.guardian)
    db.execute("""INSERT INTO consents(id,identity_id,token_hash,status,method,scopes,purpose,statement_version,
                  statement_hash,signer_role,created_at,expires_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
               (cid, iid, token_hash(token), "pending", "link", db.jdump(scopes), purpose,
                policy.CONSENT_STATEMENT_VERSION, hashlib.sha256(statement.encode()).hexdigest(),
                "representante_legal" if body.guardian else "interesado", db.now(),
                db.now() + settings.consent_link_days * 86400))
    db.audit("solicitud_consentimiento", user["id"], identity=iid, consent=cid)
    base = settings.public_base_url or ""
    return {"consent_id": cid, "link": f"{base}/#/consentimiento/{token}", "token": token,
            "expires_in_days": settings.consent_link_days, "statement": statement}


@router.post("/{iid}/revoke")
def revoke_identity(iid: str, user=Depends(current_user)) -> dict[str, Any]:
    ident = services.load_identity(iid, user["id"])
    _revoke(ident, "titular_cuenta")
    return _out(db.one("SELECT * FROM identities WHERE id=?", (iid,)))


def _revoke(ident: dict[str, Any], by: str) -> int:
    db.execute("UPDATE identities SET consent_status='revoked', revoked_at=? WHERE id=?", (db.now(), ident["id"]))
    db.execute("UPDATE consents SET status='revoked', revoked_at=? WHERE identity_id=? AND status IN ('granted','pending')",
               (db.now(), ident["id"]))
    storage.delete_identity_files(ident["user_id"], ident["id"])
    affected = services.purge_identity_from_projects(ident["id"], ident["user_id"])
    db.audit("consentimiento_revocado", ident["user_id"], identity=ident["id"], by=by, projects=affected)
    return affected


@router.delete("/{iid}")
def delete_identity(iid: str, user=Depends(current_user)) -> dict[str, bool]:
    ident = services.load_identity(iid, user["id"])
    services.purge_identity_from_projects(iid, user["id"])
    storage.delete_identity_files(user["id"], iid)
    db.execute("DELETE FROM identities WHERE id=?", (ident["id"],))
    db.audit("identidad_eliminada", user["id"], identity=iid)
    return {"ok": True}


# ------------------------------------------------------------ página pública de consentimiento

def _consent_by_token(token: str) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    c = db.one("SELECT * FROM consents WHERE token_hash=?", (token_hash(token),))
    if not c:
        raise NotFound("Enlace de consentimiento no válido.")
    ident = db.one("SELECT * FROM identities WHERE id=?", (c["identity_id"],))
    owner = db.one("SELECT email FROM users WHERE id=?", (ident["user_id"],))
    ident["scopes"] = db.jload(ident["scopes"], [])
    c["scopes"] = db.jload(c["scopes"], [])
    return c, ident, owner


@public.get("/{token}")
def consent_info(token: str) -> dict[str, Any]:
    c, ident, owner = _consent_by_token(token)
    guardian = c["signer_role"] == "representante_legal"
    return {"status": c["status"] if not (c["status"] == "pending" and c["expires_at"] < db.now()) else "expired",
            "identity_name": ident["display_name"], "requester": owner["email"], "purpose": c["purpose"],
            "scopes": c["scopes"], "guardian": guardian,
            "statement": policy.consent_statement(ident["display_name"], c["scopes"], c["purpose"], owner["email"],
                                                  guardian)}


class Grant(BaseModel):
    full_name: str
    accept: bool
    confirm_adult: bool


@public.post("/{token}/grant")
def consent_grant(token: str, body: Grant, request: Request) -> dict[str, Any]:
    c, ident, owner = _consent_by_token(token)
    if c["status"] != "pending" or c["expires_at"] < db.now():
        raise ConsentError("Este enlace ya no admite respuestas (usado, caducado o retirado).")
    if not (body.accept and body.confirm_adult and body.full_name.strip()):
        raise ConsentError("Debe escribir su nombre completo, confirmar que es mayor de edad y aceptar el texto.")
    guardian = c["signer_role"] == "representante_legal"
    statement = policy.consent_statement(ident["display_name"], c["scopes"], c["purpose"], owner["email"], guardian)
    if hashlib.sha256(statement.encode()).hexdigest() != c["statement_hash"]:
        raise ConsentError("El texto del consentimiento ha cambiado; solicite un enlace nuevo.")
    db.execute("""UPDATE consents SET status='granted', granted_at=?, signer_name=?, ip_hash=?, ua_hash=? WHERE id=?""",
               (db.now(), body.full_name.strip()[:120], pseudonymize(client_ip(request)),
                pseudonymize(request.headers.get("user-agent")), c["id"]))
    scopes = sorted(set(ident["scopes"]) | set(c["scopes"])) if ident["consent_status"] == "verified" else c["scopes"]
    db.execute("UPDATE identities SET consent_status='verified', scopes=?, revoked_at=NULL WHERE id=?",
               (db.jdump(scopes), ident["id"]))
    db.audit("consentimiento_otorgado", ident["user_id"], identity=ident["id"], consent=c["id"])
    return {"ok": True, "message": "Consentimiento registrado. Puede retirarlo en cualquier momento con este enlace."}


@public.post("/{token}/revoke")
def consent_revoke(token: str) -> dict[str, Any]:
    c, ident, _ = _consent_by_token(token)
    if c["status"] == "revoked":
        return {"ok": True, "message": "El consentimiento ya estaba retirado."}
    ident["scopes"] = db.jload(ident["scopes"], []) if isinstance(ident["scopes"], str) else ident["scopes"]
    _revoke(ident, "interesado")
    return {"ok": True, "message": "Consentimiento retirado. Se han eliminado sus fotografías y no se podrán "
                                   "procesar ni exportar nuevos vídeos con su imagen o voz."}
