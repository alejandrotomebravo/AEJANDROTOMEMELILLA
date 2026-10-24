from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, UploadFile
from pydantic import BaseModel

from .. import db, policy, services, storage
from ..config import settings
from ..errors import AppError, Conflict, NotFound
from ..jobs import queue
from ..gen import styles
from ..media import ffmpeg, timeline, watermark
from .deps import current_user

router = APIRouter(prefix="/api/projects", tags=["proyectos"])

ASPECTS = {"9:16", "16:9", "1:1"}


class NewProject(BaseModel):
    name: str
    aspect: str = "9:16"
    style: str = "caricatura"
    resolution: str = "1080p"
    fps: int = 30
    codec: str = "h264"


class ProjectPatch(BaseModel):
    name: str | None = None
    aspect: str | None = None
    style: str | None = None
    brief: str | None = None
    export: dict[str, Any] | None = None
    watermark: dict[str, Any] | None = None
    pronunciations: dict[str, str] | None = None


class TimelineIn(BaseModel):
    version: int
    timeline: dict[str, Any]


def _summary(p: dict[str, Any]) -> dict[str, Any]:
    thumb = db.one("SELECT portrait_preview_asset_id AS t FROM cast_members WHERE project_id=? AND portrait_preview_asset_id "
                   "IS NOT NULL LIMIT 1", (p["id"],))
    stats = db.one("SELECT COUNT(*) AS n, COALESCE(SUM(size),0) AS bytes FROM assets WHERE project_id=?", (p["id"],))
    n_src = db.one("SELECT COUNT(*) AS n FROM sources WHERE project_id=? AND status='ready'", (p["id"],))["n"]
    n_exp = db.one("SELECT COUNT(*) AS n FROM assets WHERE project_id=? AND kind='render'", (p["id"],))["n"]
    n_cast = db.one("SELECT COUNT(*) AS n FROM cast_members WHERE project_id=?", (p["id"],))["n"]
    return {"id": p["id"], "name": p["name"], "aspect": p["aspect"], "updated_at": p["updated_at"],
            "created_at": p["created_at"], "has_video": n_src > 0, "clips": n_src, "exports": n_exp, "cast": n_cast,
            "storage_bytes": stats["bytes"], "files": stats["n"],
            "expires_at": (p["updated_at"] + settings.retention_days * 86400) if settings.retention_days else None,
            "thumb_asset_id": thumb["t"] if thumb else None}


def cast_out(c: dict[str, Any]) -> dict[str, Any]:
    return {k: c[k] for k in ("id", "identity_id", "name", "description", "style", "voice_id", "status", "error",
                              "portrait_preview_asset_id", "created_at")} | {"provider": (c.get("meta") or {}).get("provider")}


def _full(p: dict[str, Any]) -> dict[str, Any]:
    clips = db.all_("SELECT id, label, duration, width, height, fps, review_asset_id, origin FROM sources "
                    "WHERE project_id=? AND status='ready'", (p["id"],))
    for c in clips:
        o = db.jload(c.pop("origin"), {})
        c.update({"scene_id": o.get("scene_id"), "provider": o.get("provider")})
    sb = p["storyboard"] or {}
    return {
        "id": p["id"], "name": p["name"], "aspect": p["aspect"], "settings": p["settings"],
        "style": services.project_style(p), "brief": p["brief"], "use_declaration": p["use_declaration"],
        "storyboard": {"title": sb.get("title", ""), "logline": sb.get("logline", ""), "provider": sb.get("provider"),
                       "model": sb.get("model"), "scenes": sb.get("scenes", [])},
        "cast": [cast_out(c) for c in services.list_cast(p["id"])],
        "clips": clips, "has_video": bool(clips),
        "timeline": p["timeline"], "timeline_version": p["timeline_version"],
        "watermark": services.watermark_cfg(p), "real_people": services.real_people_in_project(p["id"]),
        "created_at": p["created_at"], "updated_at": p["updated_at"],
    }


@router.get("")
def list_projects(user=Depends(current_user)) -> list[dict[str, Any]]:
    rows = db.all_("SELECT * FROM projects WHERE user_id=? ORDER BY updated_at DESC", (user["id"],))
    return [_summary(r) for r in rows]


@router.post("/{pid}/duplicate")
def duplicate_project(pid: str, user=Depends(current_user)) -> dict[str, Any]:
    """Duplica la configuración, las instrucciones y la declaración de uso (no copia personajes ni vídeos)."""
    p = services.load_project(pid, user["id"])
    nid = db.new_id()
    db.execute("""INSERT INTO projects(id,user_id,name,aspect,settings,use_declaration,brief,timeline,created_at,updated_at)
                  VALUES (?,?,?,?,?,?,?,?,?,?)""",
               (nid, user["id"], f"{p['name']} (copia)"[:120], p["aspect"], db.jdump(p["settings"]),
                db.jdump(p["use_declaration"]) if p["use_declaration"] else None, p["brief"], db.jdump(timeline.empty()),
                db.now(), db.now()))
    return _full(services.load_project(nid, user["id"]))


@router.post("")
def create_project(body: NewProject, user=Depends(current_user)) -> dict[str, Any]:
    name = body.name.strip()[:120]
    if not name:
        raise AppError("El proyecto necesita un nombre.")
    if body.aspect not in ASPECTS:
        raise AppError("Formato no válido. Elija 9:16, 16:9 o 1:1.")
    styles.get(body.style)
    pid = db.new_id()
    settings_ = {"style": body.style, "export": {"resolution": body.resolution, "fps": body.fps, "codec": body.codec},
                 "watermark": dict(watermark.DEFAULT_WATERMARK), "pronunciations": {}}
    db.execute("""INSERT INTO projects(id,user_id,name,aspect,settings,timeline,created_at,updated_at)
                  VALUES (?,?,?,?,?,?,?,?)""",
               (pid, user["id"], name, body.aspect, db.jdump(settings_), db.jdump(timeline.empty()), db.now(), db.now()))
    db.audit("proyecto_creado", user["id"], pid)
    return _full(services.load_project(pid, user["id"]))


@router.get("/{pid}")
def get_project(pid: str, user=Depends(current_user)) -> dict[str, Any]:
    return _full(services.load_project(pid, user["id"]))


@router.patch("/{pid}")
def patch_project(pid: str, body: ProjectPatch, user=Depends(current_user)) -> dict[str, Any]:
    p = services.load_project(pid, user["id"])
    fields: dict[str, Any] = {}
    s = dict(p["settings"])
    if body.name is not None:
        if not body.name.strip():
            raise AppError("El nombre no puede estar vacío.")
        fields["name"] = body.name.strip()[:120]
    if body.aspect is not None:
        if body.aspect not in ASPECTS:
            raise AppError("Formato no válido.")
        fields["aspect"] = body.aspect
    if body.style is not None:
        styles.get(body.style)
        s["style"] = body.style
    if body.brief is not None:
        brief = body.brief.strip()[:6000]
        if brief:
            policy.check_instructions(brief)
        fields["brief"] = brief
    if body.export is not None:
        s["export"] = {k: body.export[k] for k in ("resolution", "fps", "codec") if k in body.export}
    if body.watermark is not None:
        s["watermark"] = watermark.validate(body.watermark)
    if body.pronunciations is not None:
        s["pronunciations"] = {k.strip()[:40]: v.strip()[:80] for k, v in body.pronunciations.items() if k.strip()}
    fields["settings"] = s
    services.update_project(pid, **fields)
    return _full(services.load_project(pid, user["id"]))


@router.delete("/{pid}")
def delete_project(pid: str, user=Depends(current_user)) -> dict[str, bool]:
    services.load_project(pid, user["id"])
    for j in db.all_("SELECT id FROM jobs WHERE project_id=? AND status IN ('queued','running')", (pid,)):
        queue.request_cancel(j["id"])
    storage.delete_project_files(user["id"], pid)
    db.execute("DELETE FROM projects WHERE id=?", (pid,))
    db.audit("proyecto_eliminado", user["id"], pid)
    return {"ok": True}


# ------------------------------------------------------------ declaración de uso

@router.put("/{pid}/use-declaration")
def set_use_declaration(pid: str, body: dict[str, Any], user=Depends(current_user)) -> dict[str, Any]:
    services.load_project(pid, user["id"])
    clean = policy.validate_use_declaration(body)
    services.update_project(pid, use_declaration=clean)
    db.audit("declaracion_uso", user["id"], pid, **clean)
    return clean


# ------------------------------------------------------------ línea de tiempo

@router.put("/{pid}/timeline")
def save_timeline(pid: str, body: TimelineIn, user=Depends(current_user)) -> dict[str, Any]:
    p = services.load_project(pid, user["id"])
    if body.version != p["timeline_version"]:
        raise Conflict("El proyecto se ha modificado en otra ventana o por un procesamiento. Recargue los cambios.",
                       details={"server_version": p["timeline_version"]})
    known = {r["id"] for r in db.all_(
        "SELECT id FROM assets WHERE project_id=? AND kind IN ('dialogue_audio','audio','music')", (pid,))}
    tl = timeline.validate(body.timeline, services.source_durations(pid), known)
    new_version = p["timeline_version"] + 1
    cur = db.execute("UPDATE projects SET timeline=?, timeline_version=?, updated_at=? WHERE id=? AND timeline_version=?",
                     (db.jdump(tl), new_version, db.now(), pid, body.version))
    if cur.rowcount != 1:
        raise Conflict("Conflicto de versiones al guardar.")
    return {"version": new_version, "timeline": tl, "duration": timeline.output_duration(tl)}


@router.post("/{pid}/media")
def upload_media(pid: str, kind: str = "music", file: UploadFile = File(...),
                 user=Depends(current_user)) -> dict[str, Any]:
    """Música o audio adicional para las pistas del editor."""
    services.load_project(pid, user["id"])
    if kind not in ("music", "audio"):
        raise AppError("Tipo de pista no válido.")
    ext = Path(file.filename or "audio.mp3").suffix.lower() or ".mp3"
    if ext not in {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac", ".webm", ".mp4", ".caf"}:
        raise AppError("Formato de audio no admitido.")
    aid, raw = storage.new_path(user["id"], ext, project_id=pid)
    storage.save_stream(file.file, raw, max_bytes=200 * 1024 * 1024)
    try:
        dest = raw.with_name(f"{aid}.wav")
        info = ffmpeg.to_wav(raw, dest)
    except Exception:
        raise AppError("No se reconoce el archivo como audio válido.")
    finally:
        raw.unlink(missing_ok=True)
    a = storage.register(aid=aid, user_id=user["id"], project_id=pid, kind=kind, path=dest, mime="audio/wav",
                         duration=info.duration, meta={"filename": (file.filename or "")[:120]})
    return {"asset_id": a["id"], "duration": a["duration"], "name": a["meta"]["filename"]}


@router.get("/{pid}/media")
def list_media(pid: str, user=Depends(current_user)) -> list[dict[str, Any]]:
    services.load_project(pid, user["id"])
    rows = db.all_("""SELECT id, kind, duration, meta, created_at FROM assets WHERE project_id=?
                      AND kind IN ('music','audio','dialogue_audio') ORDER BY created_at""", (pid,))
    for r in rows:
        r["meta"] = db.jload(r["meta"], {})
    return rows
