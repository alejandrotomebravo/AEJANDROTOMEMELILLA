"""Acceso a archivos mediante URL firmadas de corta duración.

Los elementos <video>/<audio> del navegador no pueden enviar la cabecera de
autorización, así que el cliente pide una URL firmada (válida 1 h) para cada
recurso. Los vídeos intermedios sin marca de agua nunca se sirven al usuario;
sólo los fragmentos temporales que se envían al proveedor remoto de
sincronización (propósito «provider»).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse

from .. import db, storage
from ..errors import Forbidden, NotFound
from ..security import sign_asset, verify_asset_signature
from .deps import current_user

router = APIRouter(prefix="/api", tags=["archivos"])

DOWNLOAD_NAMES = {"render": "video_ia.mp4", "subtitles": "subtitulos.srt", "manifest": "procedencia.json"}


@router.get("/assets/{aid}/url")
def asset_url(aid: str, download: bool = False, user=Depends(current_user)) -> dict[str, Any]:
    a = storage.get_owned(aid, user["id"])
    if a["kind"] not in storage.SERVABLE_KINDS:
        raise Forbidden("Este archivo intermedio no puede descargarse: utilice la vista previa o la exportación, "
                        "que incluyen la marca de agua.")
    purpose = "download" if download else "view"
    return {"url": f"/api/files/{sign_asset(aid, 3600, purpose)}", "expires_in": 3600}


@router.get("/files/{token}")
def serve_file(token: str):
    res = verify_asset_signature(token)
    if not res:
        raise Forbidden("Enlace caducado o no válido.")
    aid, purpose = res
    a = db.one("SELECT * FROM assets WHERE id=?", (aid,))
    if not a:
        raise NotFound("Archivo no encontrado.")
    if purpose == "provider":
        if a["kind"] != "segment":
            raise Forbidden("Enlace no válido.")
    elif a["kind"] not in storage.SERVABLE_KINDS:
        raise Forbidden("Archivo no disponible.")
    path = Path(a["path"])
    if not path.exists():
        raise NotFound("Archivo no encontrado.")
    headers = {"Cache-Control": "private, max-age=3600", "X-Content-Type-Options": "nosniff"}
    if purpose == "download":
        return FileResponse(path, media_type=a["mime"] or "application/octet-stream", headers=headers,
                            filename=DOWNLOAD_NAMES.get(a["kind"], path.name))
    return FileResponse(path, media_type=a["mime"] or "application/octet-stream", headers=headers)
