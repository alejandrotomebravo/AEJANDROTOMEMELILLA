"""Operaciones de dominio compartidas por la API y los trabajos."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from . import db, storage
from .config import settings
from .errors import AppError, ConsentError, NotFound
from .media import ffmpeg, timeline, watermark

APP_VERSION = "0.1.0"


# ------------------------------------------------------------------ proyectos

def load_project(project_id: str, user_id: str) -> dict[str, Any]:
    p = db.one("SELECT * FROM projects WHERE id=? AND user_id=?", (project_id, user_id))
    if not p:
        raise NotFound("Proyecto no encontrado.")
    for k, default in (("settings", {}), ("provenance", None), ("use_declaration", None), ("timeline", {}),
                       ("video_history", []), ("analysis", None)):
        p[k] = db.jload(p[k], default)
    return p


def update_project(project_id: str, **fields: Any) -> None:
    sets, vals = [], []
    for k, v in fields.items():
        sets.append(f"{k}=?")
        vals.append(db.jdump(v) if isinstance(v, (dict, list)) else v)
    sets.append("updated_at=?")
    vals.append(db.now())
    vals.append(project_id)
    db.execute(f"UPDATE projects SET {', '.join(sets)} WHERE id=?", tuple(vals))


def watermark_cfg(project: dict[str, Any]) -> dict[str, Any]:
    return watermark.validate(project["settings"].get("watermark"))


def require_ready(project: dict[str, Any]) -> None:
    if not project.get("provenance"):
        raise AppError("Declare primero la procedencia del vídeo.", code="procedencia_pendiente")
    if not project.get("use_declaration"):
        raise AppError("Complete primero la declaración de uso responsable.", code="declaracion_pendiente")
    if not project.get("working_asset_id"):
        raise AppError("Importe primero un vídeo.", code="sin_video")


def working_path(project: dict[str, Any]) -> Path:
    return Path(storage.get(project["working_asset_id"])["path"])


def set_working(project: dict[str, Any], new_asset_id: str, operation: str,
                identities: list[str] | None = None) -> None:
    """Registra una nueva versión del vídeo procesado manteniendo el historial para poder revertir."""
    history = list(project.get("video_history") or [])
    history.append({"asset_id": new_asset_id, "operation": operation, "identities": identities or [],
                    "at": db.now()})
    update_project(project["id"], working_asset_id=new_asset_id, video_history=history)
    project["working_asset_id"] = new_asset_id
    project["video_history"] = history


def used_identities(project: dict[str, Any]) -> set[str]:
    ids: set[str] = set()
    for h in project.get("video_history") or []:
        ids.update(h.get("identities") or [])
        if h["asset_id"] == project["working_asset_id"]:
            break
    return ids


def operations(project: dict[str, Any]) -> list[str]:
    ops = []
    for h in project.get("video_history") or []:
        ops.append(h["operation"])
        if h["asset_id"] == project["working_asset_id"]:
            break
    names = {"faceswap": "sustitución facial", "lipsync": "sincronización labial"}
    return sorted({names.get(o, o) for o in ops})


def check_identities_still_valid(project: dict[str, Any], user_id: str) -> None:
    from .policy import require_identity_usable
    for iid in used_identities(project):
        ident = load_identity(iid, user_id, missing_ok=True)
        if ident is None:
            raise ConsentError("El vídeo contiene una identidad que ha sido eliminada. Revierta el procesamiento.")
        require_identity_usable(ident, "imagen")


def asset_paths_for_timeline(tl: dict[str, Any], user_id: str) -> dict[str, Path]:
    out = {}
    for aid in timeline.referenced_assets(tl):
        a = storage.get_owned(aid, user_id)
        out[aid] = Path(a["path"])
    return out


def source_duration(project: dict[str, Any]) -> float:
    if not project.get("proxy_asset_id"):
        return 0.0
    return float(storage.get(project["proxy_asset_id"])["duration"] or 0)


# ------------------------------------------------------------------ identidades

def load_identity(identity_id: str, user_id: str, missing_ok: bool = False) -> dict[str, Any] | None:
    i = db.one("SELECT * FROM identities WHERE id=? AND user_id=?", (identity_id, user_id))
    if not i:
        if missing_ok:
            return None
        raise NotFound("Identidad no encontrada.")
    i["scopes"] = db.jload(i["scopes"], [])
    i["is_minor"] = bool(i["is_minor"])
    return i


def identity_photos(identity_id: str) -> list[Path]:
    rows = db.all_("SELECT path FROM assets WHERE identity_id=? AND kind='face_photo' ORDER BY created_at",
                   (identity_id,))
    return [Path(r["path"]) for r in rows]


def purge_identity_from_projects(identity_id: str, user_id: str) -> int:
    """Tras retirar el consentimiento: elimina vistas previas/exportaciones con esa identidad y
    devuelve los proyectos afectados a su vídeo original."""
    affected = 0
    for p in db.all_("SELECT id FROM projects WHERE user_id=?", (user_id,)):
        proj = load_project(p["id"], user_id)
        hist = proj.get("video_history") or []
        if not any(identity_id in (h.get("identities") or []) for h in hist):
            continue
        affected += 1
        for h in hist:
            if h["asset_id"] != proj["proxy_asset_id"]:
                storage.delete_asset(h["asset_id"])
        for a in db.all_("SELECT id, meta FROM assets WHERE project_id=? AND kind IN ('preview','render','review')",
                         (proj["id"],)):
            storage.delete_asset(a["id"])
        db.execute("UPDATE characters SET identity_id=NULL WHERE project_id=? AND identity_id=?",
                   (proj["id"], identity_id))
        update_project(proj["id"], working_asset_id=proj["proxy_asset_id"], video_history=[],
                       review_asset_id=None)
        db.audit("proyecto_revertido_por_revocacion", user_id, proj["id"], identity_id=identity_id)
    return affected


# ------------------------------------------------------------------ utilidades multimedia

def splice(base: Path, segment: Path, start: float, end: float, out: Path) -> ffmpeg.MediaInfo:
    """Sustituye la imagen de ``base`` en [start, end] por ``segment`` conservando el audio de ``base``."""
    info = ffmpeg.probe(base)
    dur = round(end - start, 3)
    w, h, fps = info.width, info.height, info.fps or 30
    parts, fc = [], []
    if start > 0.01:
        fc.append(f"[0:v]trim=start=0:end={start},setpts=PTS-STARTPTS[pa]")
        parts.append("[pa]")
    fc.append(f"[1:v]scale={w}:{h},setsar=1,fps={fps},tpad=stop_mode=clone:stop_duration={dur},"
              f"trim=duration={dur},setpts=PTS-STARTPTS[pb]")
    parts.append("[pb]")
    if info.duration - end > 0.01:
        fc.append(f"[0:v]trim=start={end},setpts=PTS-STARTPTS[pc]")
        parts.append("[pc]")
    fc.append(f"{''.join(parts)}concat=n={len(parts)}:v=1:a=0,format=yuv420p[v]")
    ffmpeg.run([settings.ffmpeg, "-y", "-i", str(base), "-i", str(segment), "-filter_complex", ";".join(fc),
                "-map", "[v]", "-map", "0:a?", "-c:v", "libx264", "-preset", "veryfast", "-crf", "16",
                "-c:a", "copy", "-movflags", "+faststart", str(out)])
    return ffmpeg.probe(out)


def mux_audio(video: Path, audio: Path, out: Path) -> None:
    ffmpeg.run([settings.ffmpeg, "-y", "-i", str(video), "-i", str(audio), "-map", "0:v:0", "-map", "1:a:0",
                "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-shortest", str(out)])
