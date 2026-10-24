"""Estudio de generación: personajes, guion gráfico y escenas."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from .. import db, policy, services, storage
from ..ai import tts
from ..config import settings
from ..errors import AppError, Conflict, NotFound
from ..gen import images, llm, styles, video
from ..jobs import queue
from .deps import current_user
from .projects import cast_out

router = APIRouter(prefix="/api", tags=["estudio"])

LLM_COST_ESTIMATE = 0.05  # USD aproximados por guion con Claude (unos miles de tokens)


class Confirm(BaseModel):
    confirm_cost: bool = False


def _need_confirmation(cost: float, confirm: bool, what: str, extra: dict | None = None) -> None:
    if cost > 0 and not confirm:
        raise AppError(f"{what} tiene un coste estimado de {cost:.2f} USD con el proveedor de pago. Confírmelo para "
                       "continuar.", code="confirmar_coste", status=402,
                       details={"estimate": {"cost_usd": round(cost, 2), **(extra or {})}})


@router.get("/styles")
def list_styles(user=Depends(current_user)) -> list[dict[str, str]]:
    return styles.catalog()


# ------------------------------------------------------------ personajes

class CastIn(BaseModel):
    name: str
    identity_id: str | None = None
    description: str = ""
    style: str | None = None
    voice_id: str | None = None


class CastPatch(BaseModel):
    name: str | None = None
    description: str | None = None
    style: str | None = None
    voice_id: str | None = None


def _check_name(project_id: str, name: str, exclude: str | None = None) -> str:
    name = name.strip()[:40]
    if not name:
        raise AppError("El personaje necesita un nombre.")
    row = db.one("SELECT id FROM cast_members WHERE project_id=? AND lower(name)=lower(?)", (project_id, name))
    if row and row["id"] != exclude:
        raise Conflict("Ya hay un personaje con ese nombre en el proyecto.")
    return name


@router.get("/projects/{pid}/cast")
def list_cast(pid: str, user=Depends(current_user)) -> list[dict[str, Any]]:
    services.load_project(pid, user["id"])
    return [cast_out(c) for c in services.list_cast(pid)]


@router.post("/projects/{pid}/cast")
def add_cast(pid: str, body: CastIn, user=Depends(current_user)) -> dict[str, Any]:
    p = services.load_project(pid, user["id"])
    name = _check_name(pid, body.name)
    style = body.style or services.project_style(p)
    styles.get(style)
    desc = body.description.strip()[:500]
    if desc:
        policy.check_dialogue_text(desc)
    if body.identity_id:
        ident = services.load_identity(body.identity_id, user["id"])
        policy.require_identity_usable(ident, "imagen")
        if not services.identity_photos(ident["id"]):
            raise AppError("La persona no tiene fotografías. Añádalas en «Personas».", code="sin_fotos")
    if body.voice_id:
        tts.get_voice(body.voice_id)
    cid = db.new_id()
    db.execute("""INSERT INTO cast_members(id,project_id,identity_id,name,description,style,voice_id,created_at)
                  VALUES (?,?,?,?,?,?,?,?)""", (cid, pid, body.identity_id, name, desc, style, body.voice_id, db.now()))
    db.audit("personaje_creado", user["id"], pid, cast=cid, identity=body.identity_id)
    services.touch_project(pid)
    return cast_out(services.load_cast(cid, pid))


@router.patch("/projects/{pid}/cast/{cid}")
def patch_cast(pid: str, cid: str, body: CastPatch, user=Depends(current_user)) -> dict[str, Any]:
    services.load_project(pid, user["id"])
    c = services.load_cast(cid, pid)
    fields: dict[str, Any] = {}
    if body.name is not None and body.name.strip() != c["name"]:
        fields["name"] = _check_name(pid, body.name, exclude=cid)
    if body.description is not None:
        if body.description.strip():
            policy.check_dialogue_text(body.description)
        fields["description"] = body.description.strip()[:500]
    if body.style is not None and body.style != c["style"]:
        styles.get(body.style)
        fields["style"] = body.style
        fields["status"] = "pending"  # el retrato actual ya no corresponde al estilo
    if body.voice_id is not None:
        if body.voice_id:
            tts.get_voice(body.voice_id)
        fields["voice_id"] = body.voice_id or None
    if fields:
        db.execute(f"UPDATE cast_members SET {', '.join(f'{k}=?' for k in fields)} WHERE id=?", (*fields.values(), cid))
    return cast_out(services.load_cast(cid, pid))


@router.delete("/projects/{pid}/cast/{cid}")
def delete_cast(pid: str, cid: str, user=Depends(current_user)) -> dict[str, bool]:
    services.load_project(pid, user["id"])
    c = services.load_cast(cid, pid)
    for k in ("portrait_asset_id", "portrait_preview_asset_id"):
        if c[k]:
            storage.delete_asset(c[k])
    db.execute("DELETE FROM cast_members WHERE id=?", (cid,))
    return {"ok": True}


@router.post("/projects/{pid}/cast/{cid}/portrait")
def generate_portrait(pid: str, cid: str, body: Confirm, user=Depends(current_user)) -> dict[str, Any]:
    p = services.load_project(pid, user["id"])
    services.require_declaration(p)
    c = services.load_cast(cid, pid)
    if c["identity_id"]:
        policy.require_identity_usable(services.load_identity(c["identity_id"], user["id"]), "imagen")
    st = images.status()
    if not st["available"]:
        raise AppError(st["reason"], code="proveedor_no_disponible", status=503)
    _need_confirmation(st.get("cost_per_image", 0) if st.get("paid") else 0, body.confirm_cost, "El retrato")
    job = queue.enqueue("portrait", user["id"], pid, {"cast_id": cid}, message=f"Retrato de {c['name']}")
    return {"job": job, "provider": st["provider"]}


# ------------------------------------------------------------ guion

class StoryIn(BaseModel):
    confirm_cost: bool = False
    max_scenes: int | None = None
    target_seconds: int | None = None


@router.post("/projects/{pid}/storyboard/generate")
def generate_storyboard(pid: str, body: StoryIn, user=Depends(current_user)) -> dict[str, Any]:
    p = services.load_project(pid, user["id"])
    services.require_declaration(p)
    if not p["brief"].strip():
        raise AppError("Escriba primero las instrucciones del vídeo.", code="instrucciones_vacias")
    policy.check_instructions(p["brief"])
    services.require_cast_usable(p, user["id"])
    st = llm.status()
    if not st["available"]:
        raise AppError(st["reason"], code="proveedor_no_disponible", status=503)
    _need_confirmation(LLM_COST_ESTIMATE if st.get("paid") else 0, body.confirm_cost, "Escribir el guion con Claude")
    n = max(1, min(settings.max_scenes, body.max_scenes or 6))
    job = queue.enqueue("storyboard", user["id"], pid, {"max_scenes": n, "target_seconds": body.target_seconds},
                        message="Escribiendo el guion")
    return {"job": job, "provider": st["provider"]}


class StoryEdit(BaseModel):
    title: str = ""
    logline: str = ""
    scenes: list[dict[str, Any]]


@router.put("/projects/{pid}/storyboard")
def save_storyboard(pid: str, body: StoryEdit, user=Depends(current_user)) -> dict[str, Any]:
    """Edición manual del guion. Los fotogramas y clips existentes se conservan por id de escena;
    los de escenas eliminadas se borran."""
    p = services.load_project(pid, user["id"])
    cast = services.list_cast(pid)
    for s in body.scenes:
        for field in ("description", "keyframe_prompt", "video_prompt"):
            if s.get(field):
                policy.check_dialogue_text(str(s[field]))
        for d in s.get("dialogue", []):
            policy.check_dialogue_text(str(d.get("line", "")))
    current = {s["id"]: s for s in services.scenes(p)}
    for s in body.scenes:  # el cliente no puede fijar fotogramas ni clips
        old = current.get(s.get("id"))
        s["keyframe"] = old.get("keyframe") if old else None
        s["clip"] = old.get("clip") if old else None
    board = llm.normalize({"title": body.title, "logline": body.logline, "scenes": body.scenes,
                           "provider": p["storyboard"].get("provider"), "model": p["storyboard"].get("model")},
                          cast, settings.max_scenes)
    kept = {s["id"] for s in board["scenes"]}
    from ..jobs.tasks import _discard_scene_media
    services.update_project(pid, storyboard=board)
    for sid, s in current.items():
        if sid not in kept:
            _discard_scene_media(pid, user["id"], s)
    return board


class Rewrite(BaseModel):
    instruction: str
    confirm_cost: bool = False


@router.post("/projects/{pid}/scenes/{sid}/rewrite")
def rewrite_scene(pid: str, sid: str, body: Rewrite, user=Depends(current_user)) -> dict[str, Any]:
    p = services.load_project(pid, user["id"])
    services.find_scene(p, sid)
    policy.check_instructions(body.instruction)
    st = llm.status()
    _need_confirmation(LLM_COST_ESTIMATE if st.get("paid") else 0, body.confirm_cost, "Reescribir la escena con Claude")
    return {"job": queue.enqueue("rewrite_scene", user["id"], pid, {"scene_id": sid, "instruction": body.instruction[:1000]},
                                 message="Reescribiendo la escena")}


# ------------------------------------------------------------ escenas

def _scene_checks(p: dict[str, Any], sid: str, user_id: str) -> dict[str, Any]:
    services.require_declaration(p)
    _, scene = services.find_scene(p, sid)
    services.require_cast_usable(p, user_id, scene.get("characters", []))
    return scene


@router.post("/projects/{pid}/scenes/{sid}/keyframe")
def make_keyframe(pid: str, sid: str, body: Confirm, user=Depends(current_user)) -> dict[str, Any]:
    p = services.load_project(pid, user["id"])
    scene = _scene_checks(p, sid, user["id"])
    st = images.status()
    if not st["available"]:
        raise AppError(st["reason"], code="proveedor_no_disponible", status=503)
    _need_confirmation(st.get("cost_per_image", 0) if st.get("paid") else 0, body.confirm_cost, "El fotograma clave")
    return {"job": queue.enqueue("keyframe", user["id"], pid, {"scene_id": sid}, message=f"Fotograma: {scene['title']}"),
            "provider": st["provider"]}


@router.post("/projects/{pid}/scenes/{sid}/animate")
def make_clip(pid: str, sid: str, body: Confirm, user=Depends(current_user)) -> dict[str, Any]:
    p = services.load_project(pid, user["id"])
    scene = _scene_checks(p, sid, user["id"])
    if not (scene.get("keyframe") or {}).get("asset_id"):
        raise AppError("Genere primero el fotograma clave.", code="fotograma_pendiente")
    video.require_available()
    est = video.estimate(scene["duration_seconds"])
    _need_confirmation(est["cost_usd"], body.confirm_cost, "El clip de vídeo", est)
    return {"job": queue.enqueue("animate", user["id"], pid, {"scene_id": sid}, message=f"Clip: {scene['title']}"),
            "estimate": est}


@router.get("/projects/{pid}/generation-estimate")
def generation_estimate(pid: str, user=Depends(current_user)) -> dict[str, Any]:
    p = services.load_project(pid, user["id"])
    img = images.status()
    pending_portraits = sum(1 for c in services.list_cast(pid) if c["status"] != "ready")
    sc = services.scenes(p)
    pending_kf = sum(1 for s in sc if not s.get("keyframe"))
    pending_clips = [s for s in sc if not s.get("clip")]
    img_cost = img.get("cost_per_image", 0) if img.get("paid") else 0
    clip_cost = sum(video.estimate(s["duration_seconds"])["cost_usd"] for s in pending_clips)
    wait = sum(video.estimate(s["duration_seconds"])["estimated_wait_seconds"] for s in pending_clips)
    return {"image_provider": img["provider"], "video_provider": video.provider(), "llm_provider": llm.provider(),
            "pending": {"portraits": pending_portraits, "keyframes": pending_kf, "clips": len(pending_clips)},
            "cost_usd": round((pending_portraits + pending_kf) * img_cost + clip_cost, 2),
            "estimated_wait_seconds": wait + (pending_portraits + pending_kf) * (20 if img["provider"] == "fal" else 2),
            "video_seconds": sum(video.estimate(s["duration_seconds"])["seconds"] for s in sc)}


@router.post("/projects/{pid}/scenes/{sid}/voices")
def scene_voices(pid: str, sid: str, user=Depends(current_user)) -> dict[str, Any]:
    """Crea las voces de los diálogos de la escena con la voz asignada a cada personaje."""
    p = services.load_project(pid, user["id"])
    _, scene = services.find_scene(p, sid)
    if not scene.get("dialogue"):
        raise AppError("La escena no tiene diálogos.", code="sin_dialogos")
    voices = tts.list_voices()
    if not voices:
        raise AppError("No hay voces sintéticas configuradas en el servidor.", code="sin_voces", status=503)
    cast = {c["name"]: c for c in services.list_cast(pid)}
    for r in db.all_("SELECT id, settings, audio_asset_id FROM dialogues WHERE project_id=?", (pid,)):
        if db.jload(r["settings"], {}).get("scene_id") == sid:  # sustituye las voces anteriores de la escena
            if r["audio_asset_id"]:
                storage.delete_asset(r["audio_asset_id"])
            db.execute("DELETE FROM dialogues WHERE id=?", (r["id"],))
    jobs = []
    for line in scene["dialogue"]:
        policy.check_dialogue_text(line["line"])
        voice_id = (cast.get(line["character"]) or {}).get("voice_id") or voices[0].id
        voice = tts.get_voice(voice_id)
        did = db.new_id()
        s = {"voice_id": voice.id, "voice_name": voice.name, "language": voice.language, "speed": 1.0, "pitch": 0.0,
             "gain_db": 0.0, "voice_owner": "sintetica", "scene_id": sid, "character": line["character"],
             "label": f"{line['character']}: {line['line'][:40]}"}
        db.execute("""INSERT INTO dialogues(id,project_id,mode,text,settings,estimated_duration,created_at)
                      VALUES (?,?,?,?,?,?,?)""", (did, pid, "tts", line["line"], db.jdump(s),
                                                 tts.estimate_duration(line["line"])["seconds"], db.now()))
        jobs.append(queue.enqueue("tts", user["id"], pid, {"dialogue_id": did}, message=f"Voz de {line['character']}"))
    return {"jobs": jobs}
