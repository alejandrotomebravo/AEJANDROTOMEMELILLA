"""Almacenamiento de archivos en disco local.

Estructura: ``storage/<usuario>/<proyecto|identidades>/<asset>.<ext>``. El
cifrado en reposo se delega en el volumen (LUKS, EBS/PD cifrado, etc.); ver
docs/DESPLIEGUE.md.
"""
from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Any, BinaryIO

from . import db
from .config import settings
from .errors import LimitExceeded, NotFound
from .security import sha256_file

# Tipos de recurso que pueden descargarse/visualizarse directamente.
# Los vídeos intermedios procesados ("working", "segment") nunca se sirven sin
# marca de agua: sólo se accede a ellos a través de vistas previas o exportaciones.
SERVABLE_KINDS = {
    "source_proxy", "thumb", "face_photo", "audio", "dialogue_audio", "music",
    "preview", "review", "render", "subtitles", "manifest",
}


def _dir_for(user_id: str, project_id: str | None, identity_id: str | None) -> Path:
    base = settings.storage_dir / user_id
    if project_id:
        base = base / "projects" / project_id
    elif identity_id:
        base = base / "identities" / identity_id
    else:
        base = base / "misc"
    base.mkdir(parents=True, exist_ok=True)
    return base


def new_path(user_id: str, ext: str, project_id: str | None = None,
             identity_id: str | None = None) -> tuple[str, Path]:
    aid = db.new_id()
    ext = ext if ext.startswith(".") else f".{ext}"
    return aid, _dir_for(user_id, project_id, identity_id) / f"{aid}{ext}"


def save_stream(stream: BinaryIO, dest: Path, max_bytes: int | None = None) -> int:
    max_bytes = max_bytes or settings.max_upload_mb * 1024 * 1024
    written = 0
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    try:
        with open(tmp, "wb") as out:
            while True:
                chunk = stream.read(1024 * 1024)
                if not chunk:
                    break
                written += len(chunk)
                if written > max_bytes:
                    raise LimitExceeded(
                        f"El archivo supera el límite de {max_bytes // (1024 * 1024)} MB."
                    )
                out.write(chunk)
        tmp.replace(dest)
    finally:
        if tmp.exists():
            tmp.unlink()
    return written


def register(*, aid: str, user_id: str, kind: str, path: Path, project_id: str | None = None,
             identity_id: str | None = None, mime: str | None = None, duration: float | None = None,
             width: int | None = None, height: int | None = None, meta: dict[str, Any] | None = None,
             expires_at: float | None = None) -> dict[str, Any]:
    size = path.stat().st_size
    digest = sha256_file(path)
    db.execute(
        """INSERT INTO assets(id,user_id,project_id,identity_id,kind,path,mime,size,sha256,duration,
           width,height,meta,created_at,expires_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (aid, user_id, project_id, identity_id, kind, str(path), mime, size, digest, duration,
         width, height, db.jdump(meta or {}), db.now(), expires_at),
    )
    return get(aid)


def get(asset_id: str) -> dict[str, Any]:
    a = db.one("SELECT * FROM assets WHERE id=?", (asset_id,))
    if not a:
        raise NotFound("Recurso no encontrado.")
    a["meta"] = db.jload(a["meta"], {})
    return a


def get_owned(asset_id: str, user_id: str) -> dict[str, Any]:
    a = get(asset_id)
    if a["user_id"] != user_id:
        raise NotFound("Recurso no encontrado.")
    return a


def delete_asset(asset_id: str) -> None:
    a = db.one("SELECT path FROM assets WHERE id=?", (asset_id,))
    if not a:
        return
    p = Path(a["path"])
    if p.exists():
        p.unlink()
    db.execute("DELETE FROM assets WHERE id=?", (asset_id,))


def delete_project_files(user_id: str, project_id: str) -> None:
    d = settings.storage_dir / user_id / "projects" / project_id
    if d.exists():
        shutil.rmtree(d, ignore_errors=True)


def delete_identity_files(user_id: str, identity_id: str) -> None:
    d = settings.storage_dir / user_id / "identities" / identity_id
    if d.exists():
        shutil.rmtree(d, ignore_errors=True)
    db.execute("DELETE FROM assets WHERE identity_id=?", (identity_id,))


def delete_user_files(user_id: str) -> None:
    d = settings.storage_dir / user_id
    if d.exists():
        shutil.rmtree(d, ignore_errors=True)


def temp_dir(prefix: str = "job") -> Path:
    settings.temp_dir.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix=f"{prefix}_", dir=settings.temp_dir))
