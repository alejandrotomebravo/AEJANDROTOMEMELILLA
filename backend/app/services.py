"""Operaciones de dominio compartidas por la API y los trabajos."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from . import db, storage
from .config import settings
from .errors import AppError, ConsentError, NotFound
from .media import ffmpeg, timeline, watermark

APP_VERSION = "0.2.0"

_SOURCE_JSON = (("origin", {}), ("provenance", None), ("fragment", None), ("video_history", []), ("analysis", None))


# ------------------------------------------------------------------ proyectos

def load_project(project_id: str, user_id: str) -> dict[str, Any]:
    p = db.one("SELECT * FROM projects WHERE id=? AND user_id=?", (project_id, user_id))
    if not p:
        raise NotFound("Proyecto no encontrado.")
    for k, default in (("settings", {}), ("use_declaration", None), ("timeline", {})):
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


def touch_project(project_id: str) -> None:
    db.execute("UPDATE projects SET updated_at=? WHERE id=?", (db.now(), project_id))


def require_declaration(project: dict[str, Any]) -> None:
    if not project.get("use_declaration"):
        raise AppError("Complete primero la declaración de uso responsable.", code="declaracion_pendiente")


# ------------------------------------------------------------------ fuentes de vídeo

def _parse_source(s: dict[str, Any]) -> dict[str, Any]:
    for k, default in _SOURCE_JSON:
        s[k] = db.jload(s[k], default)
    return s


def load_source(source_id: str, user_id: str, project_id: str | None = None) -> dict[str, Any]:
    s = db.one("SELECT * FROM sources WHERE id=? AND user_id=?", (source_id, user_id))
    if not s or (project_id and s["project_id"] != project_id):
        raise NotFound("Fuente de vídeo no encontrada.")
    return _parse_source(s)


def list_sources(project_id: str) -> list[dict[str, Any]]:
    return [_parse_source(s) for s in db.all_("SELECT * FROM sources WHERE project_id=? ORDER BY created_at", (project_id,))]


def update_source(source_id: str, **fields: Any) -> None:
    sets, vals = [], []
    for k, v in fields.items():
        sets.append(f"{k}=?")
        vals.append(db.jdump(v) if isinstance(v, (dict, list)) else v)
    vals.append(source_id)
    db.execute(f"UPDATE sources SET {', '.join(sets)} WHERE id=?", tuple(vals))


def require_source_ready(source: dict[str, Any]) -> None:
    if not source.get("provenance"):
        raise AppError(f"Declare la procedencia de «{source['label']}» antes de procesarla.", code="procedencia_pendiente")
    if source["status"] != "ready" or not source.get("working_asset_id"):
        raise AppError(f"«{source['label']}» todavía no está lista.", code="fuente_no_lista")


def source_working_path(source: dict[str, Any]) -> Path:
    return Path(storage.get(source["working_asset_id"])["path"])


def set_source_working(source: dict[str, Any], new_asset_id: str, operation: str,
                       identities: list[str] | None = None) -> None:
    """Nueva versión procesada de una fuente, con historial para poder revertir."""
    history = list(source.get("video_history") or [])
    # Al procesar desde una versión anterior se descartan las versiones posteriores.
    if source["working_asset_id"] != source["proxy_asset_id"]:
        idx = next((i for i, h in enumerate(history) if h["asset_id"] == source["working_asset_id"]), len(history) - 1)
        history = history[: idx + 1]
    else:
        history = []
    history.append({"asset_id": new_asset_id, "operation": operation, "identities": identities or [], "at": db.now()})
    update_source(source["id"], working_asset_id=new_asset_id, video_history=history)
    source["working_asset_id"] = new_asset_id
    source["video_history"] = history


def _active_history(source: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for h in source.get("video_history") or []:
        if source["working_asset_id"] == source["proxy_asset_id"]:
            break
        out.append(h)
        if h["asset_id"] == source["working_asset_id"]:
            break
    return out


def used_identities(project_id: str) -> set[str]:
    ids: set[str] = set()
    for s in list_sources(project_id):
        for h in _active_history(s):
            ids.update(h.get("identities") or [])
    return ids


def operations(project_id: str) -> list[str]:
    names = {"faceswap": "sustitución facial", "lipsync": "sincronización labial"}
    ops = {names.get(h["operation"], h["operation"]) for s in list_sources(project_id) for h in _active_history(s)}
    return sorted(ops)


def faces_altered(project_id: str) -> bool:
    return any(h["operation"] == "faceswap" for s in list_sources(project_id) for h in _active_history(s))


def watermark_cfg(project: dict[str, Any]) -> dict[str, Any]:
    return watermark.with_required(project["settings"].get("watermark"), faces_altered=faces_altered(project["id"]))


def check_identities_still_valid(project_id: str, user_id: str) -> None:
    from .policy import require_identity_usable
    for iid in used_identities(project_id):
        ident = load_identity(iid, user_id, missing_ok=True)
        if ident is None:
            raise ConsentError("El vídeo contiene una identidad eliminada. Revierta el procesamiento de esa fuente.")
        require_identity_usable(ident, "imagen")


def source_durations(project_id: str) -> dict[str, float]:
    return {s["id"]: float(s["duration"] or 0) for s in list_sources(project_id) if s["status"] == "ready"}


def timeline_sources(project: dict[str, Any], user_id: str) -> dict[str, Path]:
    out = {}
    for sid in timeline.used_sources(project["timeline"]):
        s = load_source(sid, user_id, project["id"])
        require_source_ready(s)
        out[sid] = source_working_path(s)
    return out


def asset_paths_for_timeline(tl: dict[str, Any], user_id: str) -> dict[str, Path]:
    return {aid: Path(storage.get_owned(aid, user_id)["path"]) for aid in timeline.referenced_assets(tl)}


def credits(project: dict[str, Any]) -> list[str]:
    """Créditos de las fuentes usadas en el montaje que tienen autor o exigen atribución."""
    used = set(timeline.used_sources(project["timeline"]))
    lines = []
    for s in list_sources(project["id"]):
        if s["id"] not in used:
            continue
        o = s["origin"] or {}
        lic = o.get("license") or {}
        if o.get("attribution") and (lic.get("requires_attribution") or o.get("author")):
            lines.append(o["attribution"][:200])
        elif s.get("provenance") and s["provenance"].get("rights_holder") and s["provenance"].get("source_type") != "propio":
            lines.append(f"«{s['label']}» · {s['provenance']['rights_holder']}")
    return lines


def credits_required(project: dict[str, Any]) -> bool:
    used = set(timeline.used_sources(project["timeline"]))
    return any(((s["origin"] or {}).get("license") or {}).get("requires_attribution")
               for s in list_sources(project["id"]) if s["id"] in used)


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
    rows = db.all_("SELECT path FROM assets WHERE identity_id=? AND kind='face_photo' ORDER BY created_at", (identity_id,))
    return [Path(r["path"]) for r in rows]


def purge_identity_from_projects(identity_id: str, user_id: str) -> int:
    """Tras retirar el consentimiento: borra versiones procesadas, vistas previas y exportaciones que
    contienen a esa persona y devuelve las fuentes afectadas a su vídeo original."""
    affected_projects: set[str] = set()
    for row in db.all_("SELECT id FROM sources WHERE user_id=?", (user_id,)):
        s = load_source(row["id"], user_id)
        hist = s.get("video_history") or []
        if not any(identity_id in (h.get("identities") or []) for h in hist):
            continue
        affected_projects.add(s["project_id"])
        for h in hist:
            storage.delete_asset(h["asset_id"])
        if s.get("review_asset_id"):
            storage.delete_asset(s["review_asset_id"])
        update_source(s["id"], working_asset_id=s["proxy_asset_id"], video_history=[], review_asset_id=None)
        db.audit("fuente_revertida_por_revocacion", user_id, s["project_id"], source=s["id"], identity_id=identity_id)
    for pid in affected_projects:
        for a in db.all_("SELECT id FROM assets WHERE project_id=? AND kind IN ('preview','render')", (pid,)):
            storage.delete_asset(a["id"])
    db.execute("UPDATE characters SET identity_id=NULL WHERE identity_id=?", (identity_id,))
    return len(affected_projects)


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
