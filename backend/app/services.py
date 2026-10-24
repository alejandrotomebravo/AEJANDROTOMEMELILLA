"""Operaciones de dominio compartidas por la API y los trabajos."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from . import db, storage
from .errors import AppError, ConsentError, NotFound
from .media import timeline, watermark

APP_VERSION = "0.3.0"


# ------------------------------------------------------------------ proyectos

def load_project(project_id: str, user_id: str) -> dict[str, Any]:
    p = db.one("SELECT * FROM projects WHERE id=? AND user_id=?", (project_id, user_id))
    if not p:
        raise NotFound("Proyecto no encontrado.")
    for k, default in (("settings", {}), ("use_declaration", None), ("timeline", {}), ("storyboard", {})):
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


def project_style(project: dict[str, Any]) -> str:
    from .gen import styles
    return project["settings"].get("style") or styles.DEFAULT_STYLE


# ------------------------------------------------------------------ reparto

def load_cast(cast_id: str, project_id: str) -> dict[str, Any]:
    c = db.one("SELECT * FROM cast_members WHERE id=? AND project_id=?", (cast_id, project_id))
    if not c:
        raise NotFound("Personaje no encontrado.")
    c["meta"] = db.jload(c["meta"], {})
    return c


def list_cast(project_id: str, with_paths: bool = False) -> list[dict[str, Any]]:
    rows = db.all_("SELECT * FROM cast_members WHERE project_id=? ORDER BY created_at", (project_id,))
    for c in rows:
        c["meta"] = db.jload(c["meta"], {})
        if with_paths and c.get("portrait_asset_id"):
            a = db.one("SELECT path FROM assets WHERE id=?", (c["portrait_asset_id"],))
            c["portrait_path"] = a["path"] if a else None
    return rows


def require_cast_usable(project: dict[str, Any], user_id: str, names: list[str] | None = None) -> None:
    """Comprueba el consentimiento vigente de todas las personas reales del reparto (o de las indicadas)."""
    from .policy import require_identity_usable
    for c in list_cast(project["id"]):
        if names is not None and c["name"] not in names:
            continue
        if c["identity_id"]:
            ident = load_identity(c["identity_id"], user_id, missing_ok=True)
            if ident is None:
                raise ConsentError(f"La persona de «{c['name']}» se ha eliminado. Quite el personaje o elija otra.")
            require_identity_usable(ident, "imagen")


def real_people_in_project(project_id: str) -> bool:
    return bool(db.one("SELECT 1 FROM cast_members WHERE project_id=? AND identity_id IS NOT NULL LIMIT 1",
                       (project_id,)))


def watermark_cfg(project: dict[str, Any]) -> dict[str, Any]:
    return watermark.with_required(project["settings"].get("watermark"),
                                   faces_altered=real_people_in_project(project["id"]))


# ------------------------------------------------------------------ guion y escenas

def scenes(project: dict[str, Any]) -> list[dict[str, Any]]:
    return list((project.get("storyboard") or {}).get("scenes") or [])


def find_scene(project: dict[str, Any], scene_id: str) -> tuple[int, dict[str, Any]]:
    for i, s in enumerate(scenes(project)):
        if s["id"] == scene_id:
            return i, s
    raise NotFound("Escena no encontrada.")


def save_scene(project_id: str, user_id: str, scene_id: str, **fields: Any) -> dict[str, Any]:
    """Actualiza una escena releyendo el proyecto (evita pisar cambios concurrentes de otros trabajos)."""
    with db.tx():
        p = load_project(project_id, user_id)
        board = p["storyboard"]
        for s in board.get("scenes", []):
            if s["id"] == scene_id:
                s.update(fields)
                db.execute("UPDATE projects SET storyboard=?, updated_at=? WHERE id=?",
                           (db.jdump(board), db.now(), project_id))
                return s
    raise NotFound("Escena no encontrada.")


# ------------------------------------------------------------------ clips generados (fuentes)

def load_source(source_id: str, user_id: str, project_id: str | None = None) -> dict[str, Any]:
    s = db.one("SELECT * FROM sources WHERE id=? AND user_id=?", (source_id, user_id))
    if not s or (project_id and s["project_id"] != project_id):
        raise NotFound("Clip no encontrado.")
    s["origin"] = db.jload(s["origin"], {})
    return s


def source_durations(project_id: str) -> dict[str, float]:
    return {s["id"]: float(s["duration"] or 0) for s in
            db.all_("SELECT id, duration FROM sources WHERE project_id=? AND status='ready'", (project_id,))}


def timeline_sources(project: dict[str, Any], user_id: str) -> dict[str, Path]:
    out = {}
    for sid in timeline.used_sources(project["timeline"]):
        s = load_source(sid, user_id, project["id"])
        if s["status"] != "ready" or not s["working_asset_id"]:
            raise AppError("Hay escenas cuyo clip no está listo.", code="clip_no_listo")
        out[sid] = Path(storage.get(s["working_asset_id"])["path"])
    return out


def asset_paths_for_timeline(tl: dict[str, Any], user_id: str) -> dict[str, Path]:
    return {aid: Path(storage.get_owned(aid, user_id)["path"]) for aid in timeline.referenced_assets(tl)}


def place_scene_clip(project_id: str, user_id: str, scene_id: str, new_source_id: str, duration: float,
                     old_source_id: str | None) -> None:
    """Coloca (o sustituye) el clip de una escena en la línea de tiempo respetando el orden del guion
    y las ediciones que el usuario ya hubiera hecho sobre el clip anterior."""
    with db.tx():
        p = load_project(project_id, user_id)
        tl = p["timeline"] or timeline.empty()
        clips = tl["video"]["clips"]
        replaced = False
        for c in clips:
            if old_source_id and c["source_id"] == old_source_id:
                c.update({"source_id": new_source_id, "in": 0.0, "out": round(duration, 3)})
                replaced = True
        if old_source_id:
            for coll in (tl["dialogue"]["items"], tl["source_audio"]["mutes"], tl["subtitles"]["items"]):
                for it in coll:
                    if it.get("source_id") == old_source_id:
                        it["source_id"] = new_source_id
        if not replaced:
            order = [s["id"] for s in p["storyboard"].get("scenes", [])]
            src_scene = {s["clip"]["source_id"]: s["id"] for s in p["storyboard"].get("scenes", []) if s.get("clip")}
            pos = len(clips)
            my = order.index(scene_id) if scene_id in order else len(order)
            for i, c in enumerate(clips):
                sc = src_scene.get(c["source_id"])
                if sc in order and order.index(sc) > my:
                    pos = i
                    break
            clips.insert(pos, timeline.new_clip(new_source_id, 0.0, duration))
        db.execute("UPDATE projects SET timeline=?, timeline_version=timeline_version+1, updated_at=? WHERE id=?",
                   (db.jdump(tl), db.now(), project_id))


def remove_scene_clip(project_id: str, user_id: str, source_id: str) -> None:
    with db.tx():
        p = load_project(project_id, user_id)
        tl = timeline.remove_source(p["timeline"], source_id)
        db.execute("UPDATE projects SET timeline=?, timeline_version=timeline_version+1, updated_at=? WHERE id=?",
                   (db.jdump(tl), db.now(), project_id))


def delete_source(source_id: str) -> None:
    s = db.one("SELECT * FROM sources WHERE id=?", (source_id,))
    if not s:
        return
    for k in ("original_asset_id", "proxy_asset_id", "working_asset_id", "review_asset_id", "sprite_asset_id"):
        if s.get(k):
            storage.delete_asset(s[k])
    db.execute("DELETE FROM sources WHERE id=?", (source_id,))


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
    """Tras retirar el consentimiento: borra retratos, fotogramas, clips, vistas previas y exportaciones
    en los que aparece un personaje basado en esa persona."""
    affected = 0
    for c in db.all_("""SELECT c.* FROM cast_members c JOIN projects p ON p.id=c.project_id
                        WHERE c.identity_id=? AND p.user_id=?""", (identity_id, user_id)):
        affected += 1
        for k in ("portrait_asset_id", "portrait_preview_asset_id"):
            if c[k]:
                storage.delete_asset(c[k])
        db.execute("UPDATE cast_members SET portrait_asset_id=NULL, portrait_preview_asset_id=NULL, status='revoked', "
                   "identity_id=NULL WHERE id=?", (c["id"],))
        p = load_project(c["project_id"], user_id)
        for s in scenes(p):
            if c["name"] in s.get("characters", []):
                for part in ("keyframe", "clip"):
                    info = s.get(part) or {}
                    for k in ("asset_id", "preview_asset_id"):
                        if info.get(k):
                            storage.delete_asset(info[k])
                    if part == "clip" and info.get("source_id"):
                        remove_scene_clip(p["id"], user_id, info["source_id"])
                        delete_source(info["source_id"])
                save_scene(p["id"], user_id, s["id"], keyframe=None, clip=None)
        for a in db.all_("SELECT id FROM assets WHERE project_id=? AND kind IN ('preview','render')", (p["id"],)):
            storage.delete_asset(a["id"])
        db.audit("personaje_eliminado_por_revocacion", user_id, p["id"], cast=c["id"], identity_id=identity_id)
    return affected
