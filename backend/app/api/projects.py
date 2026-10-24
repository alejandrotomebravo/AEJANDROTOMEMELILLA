from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, UploadFile
from pydantic import BaseModel

from .. import db, policy, services, storage
from ..errors import AppError, Conflict, NotFound
from ..jobs import queue
from ..media import ffmpeg, timeline, watermark
from .deps import current_user

router = APIRouter(prefix="/api/projects", tags=["proyectos"])

ASPECTS = {"9:16", "16:9", "1:1"}
VIDEO_EXT = {".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi", ".3gp"}


class NewProject(BaseModel):
    name: str
    aspect: str = "9:16"
    resolution: str = "1080p"
    fps: int = 30
    codec: str = "h264"


class ProjectPatch(BaseModel):
    name: str | None = None
    aspect: str | None = None
    export: dict[str, Any] | None = None
    watermark: dict[str, Any] | None = None
    pronunciations: dict[str, str] | None = None


class TimelineIn(BaseModel):
    version: int
    timeline: dict[str, Any]


def _summary(p: dict[str, Any]) -> dict[str, Any]:
    thumb = None
    if p.get("proxy_asset_id"):
        thumb = db.one("SELECT thumb_asset_id FROM characters WHERE project_id=? LIMIT 1", (p["id"],))
    return {"id": p["id"], "name": p["name"], "aspect": p["aspect"], "updated_at": p["updated_at"],
            "created_at": p["created_at"], "has_video": bool(p.get("proxy_asset_id")),
            "thumb_asset_id": thumb["thumb_asset_id"] if thumb else None}


def _full(p: dict[str, Any]) -> dict[str, Any]:
    chars = db.all_("SELECT * FROM characters WHERE project_id=? ORDER BY label", (p["id"],))
    for c in chars:
        c["stats"] = db.jload(c["stats"], {})
        c.pop("track", None)
    proxy = storage.get(p["proxy_asset_id"]) if p.get("proxy_asset_id") else None
    return {
        "id": p["id"], "name": p["name"], "aspect": p["aspect"], "settings": p["settings"],
        "provenance": p["provenance"], "use_declaration": p["use_declaration"],
        "timeline": p["timeline"], "timeline_version": p["timeline_version"],
        "proxy_asset_id": p["proxy_asset_id"], "review_asset_id": p["review_asset_id"],
        "processed": p["working_asset_id"] != p["proxy_asset_id"],
        "source": ({"duration": proxy["duration"], "width": proxy["width"], "height": proxy["height"],
                    "fps": proxy["meta"].get("fps")} if proxy else None),
        "history": [{"operation": h["operation"], "at": h["at"], "index": i,
                     "current": h["asset_id"] == p["working_asset_id"]}
                    for i, h in enumerate(p["video_history"])],
        "analysis": p["analysis"], "characters": chars,
        "watermark": services.watermark_cfg(p),
        "created_at": p["created_at"], "updated_at": p["updated_at"],
    }


@router.get("")
def list_projects(user=Depends(current_user)) -> list[dict[str, Any]]:
    rows = db.all_("SELECT * FROM projects WHERE user_id=? ORDER BY updated_at DESC", (user["id"],))
    return [_summary(r) for r in rows]


@router.post("")
def create_project(body: NewProject, user=Depends(current_user)) -> dict[str, Any]:
    name = body.name.strip()[:120]
    if not name:
        raise AppError("El proyecto necesita un nombre.")
    if body.aspect not in ASPECTS:
        raise AppError("Formato no válido. Elija 9:16, 16:9 o 1:1.")
    pid = db.new_id()
    settings_ = {"export": {"resolution": body.resolution, "fps": body.fps, "codec": body.codec},
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


# ------------------------------------------------------------ procedencia y uso

@router.put("/{pid}/provenance")
def set_provenance(pid: str, body: dict[str, Any], user=Depends(current_user)) -> dict[str, Any]:
    services.load_project(pid, user["id"])
    if body.get("evidence_asset_id"):
        storage.get_owned(body["evidence_asset_id"], user["id"])
    clean = policy.validate_provenance(body)
    services.update_project(pid, provenance=clean)
    db.audit("declaracion_procedencia", user["id"], pid, **clean)
    return clean


@router.put("/{pid}/use-declaration")
def set_use_declaration(pid: str, body: dict[str, Any], user=Depends(current_user)) -> dict[str, Any]:
    services.load_project(pid, user["id"])
    clean = policy.validate_use_declaration(body)
    services.update_project(pid, use_declaration=clean)
    db.audit("declaracion_uso", user["id"], pid, **clean)
    return clean


@router.post("/{pid}/evidence")
def upload_evidence(pid: str, file: UploadFile = File(...), user=Depends(current_user)) -> dict[str, Any]:
    """Documento de licencia o autorización (PDF o imagen)."""
    services.load_project(pid, user["id"])
    ext = Path(file.filename or "").suffix.lower()
    if ext not in {".pdf", ".jpg", ".jpeg", ".png", ".heic"}:
        raise AppError("Formato no admitido para el documento (PDF, JPG, PNG o HEIC).")
    aid, dest = storage.new_path(user["id"], ext, project_id=pid)
    storage.save_stream(file.file, dest, max_bytes=20 * 1024 * 1024)
    a = storage.register(aid=aid, user_id=user["id"], project_id=pid, kind="evidence", path=dest,
                         mime=file.content_type)
    return {"asset_id": a["id"], "sha256": a["sha256"]}


# ------------------------------------------------------------ vídeo

@router.post("/{pid}/source")
def upload_source(pid: str, file: UploadFile = File(...), user=Depends(current_user)) -> dict[str, Any]:
    p = services.load_project(pid, user["id"])
    if not p["provenance"]:
        raise AppError("Antes de importar, declare la procedencia del vídeo.", code="procedencia_pendiente")
    ext = Path(file.filename or "video.mp4").suffix.lower() or ".mp4"
    if ext not in VIDEO_EXT:
        raise AppError("Formato de vídeo no admitido. Use MP4, MOV, M4V, WEBM, MKV, AVI o 3GP.")
    aid, dest = storage.new_path(user["id"], ext, project_id=pid)
    storage.save_stream(file.file, dest)
    try:
        info = ffmpeg.probe(dest)
    except Exception:
        dest.unlink(missing_ok=True)
        raise AppError("No se reconoce el archivo como vídeo válido.", code="video_no_valido")
    if not info.has_video:
        dest.unlink(missing_ok=True)
        raise AppError("El archivo no contiene una pista de vídeo.", code="sin_video")
    a = storage.register(aid=aid, user_id=user["id"], project_id=pid, kind="source", path=dest,
                         mime=file.content_type, duration=info.duration, width=info.width, height=info.height,
                         meta={"filename": (file.filename or "")[:120]})
    db.audit("video_importado", user["id"], pid, sha256=a["sha256"], size=a["size"])
    job = queue.enqueue("import_video", user["id"], pid, {"asset_id": a["id"]}, message="Importando vídeo")
    return {"job": job, "asset_id": a["id"]}


@router.get("/{pid}/analysis")
def get_analysis(pid: str, user=Depends(current_user)) -> dict[str, Any]:
    p = services.load_project(pid, user["id"])
    chars = db.all_("SELECT id,label,stats,thumb_asset_id,identity_id,track FROM characters WHERE project_id=?",
                    (pid,))
    for c in chars:
        c["stats"] = db.jload(c["stats"], {})
        track = db.jload(c.pop("track"), [])
        c["presence"] = _presence(track, (p["analysis"] or {}).get("interval", 0.5))
    return {"analysis": p["analysis"], "characters": chars}


def _presence(track: list[dict], interval: float) -> list[dict[str, float]]:
    ranges: list[dict[str, float]] = []
    for d in track:
        if ranges and d["t"] - ranges[-1]["end"] <= interval * 2.5:
            ranges[-1]["end"] = d["t"]
        else:
            ranges.append({"start": d["t"], "end": d["t"]})
    return ranges


class CharacterPatch(BaseModel):
    label: str | None = None
    identity_id: str | None = None
    clear_identity: bool = False


@router.patch("/{pid}/characters/{cid}")
def patch_character(pid: str, cid: str, body: CharacterPatch, user=Depends(current_user)) -> dict[str, Any]:
    services.load_project(pid, user["id"])
    c = db.one("SELECT * FROM characters WHERE id=? AND project_id=?", (cid, pid))
    if not c:
        raise NotFound("Personaje no encontrado.")
    if body.label is not None:
        db.execute("UPDATE characters SET label=? WHERE id=?", (body.label.strip()[:40] or c["label"], cid))
    if body.clear_identity:
        db.execute("UPDATE characters SET identity_id=NULL WHERE id=?", (cid,))
    elif body.identity_id:
        ident = services.load_identity(body.identity_id, user["id"])
        policy.require_identity_usable(ident, "imagen")
        db.execute("UPDATE characters SET identity_id=? WHERE id=?", (ident["id"], cid))
        db.audit("identidad_asignada", user["id"], pid, character=cid, identity=ident["id"])
    row = db.one("SELECT id,label,identity_id,thumb_asset_id,stats FROM characters WHERE id=?", (cid,))
    row["stats"] = db.jload(row["stats"], {})
    return row


@router.post("/{pid}/history/{index}/restore")
def restore_version(pid: str, index: int, user=Depends(current_user)) -> dict[str, Any]:
    """Vuelve a una versión anterior del vídeo procesado (index=-1: original)."""
    p = services.load_project(pid, user["id"])
    hist = p["video_history"]
    if index == -1:
        target = p["proxy_asset_id"]
    elif 0 <= index < len(hist):
        target = hist[index]["asset_id"]
    else:
        raise NotFound("Versión no encontrada.")
    services.update_project(pid, working_asset_id=target)
    job = queue.enqueue("review", user["id"], pid, {}, message="Generando copia de revisión")
    return {"ok": True, "job": job}


# ------------------------------------------------------------ línea de tiempo

@router.put("/{pid}/timeline")
def save_timeline(pid: str, body: TimelineIn, user=Depends(current_user)) -> dict[str, Any]:
    p = services.load_project(pid, user["id"])
    if body.version != p["timeline_version"]:
        raise Conflict("El proyecto se ha modificado en otra ventana o por un procesamiento. Recargue los cambios.",
                       details={"server_version": p["timeline_version"]})
    known = {r["id"] for r in db.all_(
        "SELECT id FROM assets WHERE project_id=? AND kind IN ('dialogue_audio','audio','music')", (pid,))}
    tl = timeline.validate(body.timeline, services.source_duration(p), known)
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
