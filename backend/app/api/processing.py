from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from .. import db, services
from ..ai import face_swap, lipsync
from ..errors import AppError, ConsentError, NotFound
from ..jobs import queue, tasks
from ..media import render, timeline, watermark
from ..policy import require_identity_usable
from .deps import current_user

router = APIRouter(prefix="/api", tags=["procesamiento"])


def _owned_job(job_id: str, user_id: str) -> dict[str, Any]:
    j = queue.get(job_id)
    if j["user_id"] != user_id:
        raise NotFound("Trabajo no encontrado.")
    j.pop("user_id", None)
    return j


@router.get("/jobs/{job_id}")
def get_job(job_id: str, user=Depends(current_user)) -> dict[str, Any]:
    return _owned_job(job_id, user["id"])


@router.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: str, user=Depends(current_user)) -> dict[str, Any]:
    _owned_job(job_id, user["id"])
    queue.request_cancel(job_id)
    return _owned_job(job_id, user["id"])


@router.post("/jobs/{job_id}/retry")
def retry_job(job_id: str, user=Depends(current_user)) -> dict[str, Any]:
    _owned_job(job_id, user["id"])
    queue.retry(job_id)
    return _owned_job(job_id, user["id"])


@router.get("/projects/{pid}/jobs")
def project_jobs(pid: str, user=Depends(current_user)) -> list[dict[str, Any]]:
    services.load_project(pid, user["id"])
    rows = db.all_("SELECT * FROM jobs WHERE project_id=? ORDER BY created_at DESC LIMIT 50", (pid,))
    out = []
    for r in rows:
        j = queue.public(r)
        j.pop("user_id", None)
        out.append(j)
    return out


# ------------------------------------------------------------ sustitución facial

class FaceSwapIn(BaseModel):
    character_id: str
    preview_start: float | None = None
    preview_duration: float = 3.0
    intensity: float = 1.0


@router.post("/projects/{pid}/faceswap")
def start_faceswap(pid: str, body: FaceSwapIn, user=Depends(current_user)) -> dict[str, Any]:
    p = services.load_project(pid, user["id"])
    services.require_declaration(p)
    c = db.one("SELECT * FROM characters WHERE id=? AND project_id=?", (body.character_id, pid))
    if not c:
        raise NotFound("Personaje no encontrado.")
    if not c["identity_id"]:
        raise ConsentError("Asigne una identidad autorizada al personaje antes de procesar.")
    require_identity_usable(services.load_identity(c["identity_id"], user["id"]), "imagen")
    st = face_swap.status()
    if not st["available"]:
        raise AppError(st["reason"], code="proveedor_no_disponible", status=503)
    if not 0.3 <= body.intensity <= 1.0:
        raise AppError("La intensidad debe estar entre 0,3 y 1,0.")
    services.require_source_ready(services.load_source(c["source_id"], user["id"], pid))
    params: dict[str, Any] = {"character_id": c["id"], "intensity": body.intensity}
    if body.preview_start is not None:
        params["preview"] = {"start": body.preview_start, "duration": body.preview_duration}
    stats = db.jload(c["stats"], {})
    seconds = body.preview_duration if body.preview_start is not None else stats.get("last", 0) - stats.get("first", 0) + 1
    job = queue.enqueue("faceswap", user["id"], pid, params,
                        message="Vista previa del rostro" if "preview" in params else "Sustitución facial")
    return {"job": job, "estimated_seconds": round(seconds * face_swap.seconds_per_second() + 10)}


# ------------------------------------------------------------ sincronización labial

class LipsyncIn(BaseModel):
    dialogue_id: str
    character_id: str
    source_start: float
    preview: bool = False
    accept_warnings: bool = False
    confirm_cost: bool = False
    replace_audio: bool = True


def _dialogue_duration(pid: str, did: str) -> float:
    d = db.one("SELECT duration FROM dialogues WHERE id=? AND project_id=?", (did, pid))
    if not d:
        raise NotFound("Diálogo no encontrado.")
    if not d["duration"]:
        raise AppError("El diálogo todavía no tiene audio generado.", code="dialogo_sin_audio")
    return float(d["duration"])


@router.post("/projects/{pid}/lipsync/check")
def check_lipsync(pid: str, body: LipsyncIn, user=Depends(current_user)) -> dict[str, Any]:
    p = services.load_project(pid, user["id"])
    services.require_declaration(p)
    duration = _dialogue_duration(pid, body.dialogue_id)
    if body.preview:
        duration = min(duration, 4.0)
    return tasks.lipsync_check(p, body.character_id, body.source_start, duration)


@router.post("/projects/{pid}/lipsync")
def start_lipsync(pid: str, body: LipsyncIn, user=Depends(current_user)) -> dict[str, Any]:
    p = services.load_project(pid, user["id"])
    services.require_declaration(p)
    duration = _dialogue_duration(pid, body.dialogue_id)
    if body.preview:
        duration = min(duration, 4.0)
    rep = tasks.lipsync_check(p, body.character_id, body.source_start, duration)
    if rep["blocking"]:
        raise AppError("El material no permite una sincronización aceptable.", code="calidad_insuficiente",
                       status=422, details={"report": rep})
    if rep["warnings"] and not body.accept_warnings:
        raise AppError("Revise y acepte las advertencias de calidad.", code="advertencias_pendientes",
                       status=422, details={"report": rep})
    est = rep["estimate"]
    if not est.get("available"):
        raise AppError(est.get("reason", "Sincronización labial no disponible."), code="proveedor_no_disponible",
                       status=503)
    if est.get("paid") and not body.confirm_cost:
        raise AppError(f"Este procesamiento remoto tiene un coste estimado de {est['estimated_cost_usd']:.2f} USD. "
                       "Confírmelo para continuar.", code="confirmar_coste", status=402, details={"estimate": est})
    job = queue.enqueue("lipsync", user["id"], pid, body.model_dump(),
                        message="Vista previa de sincronización" if body.preview else "Sincronización labial")
    return {"job": job, "estimate": est, "quality": rep}


# ------------------------------------------------------------ render

class RenderIn(BaseModel):
    resolution: str = "1080p"
    fps: int = 30
    codec: str = "h264"
    start: float | None = None
    end: float | None = None
    ack_disclaimer: bool = False


def rights_warnings(p: dict[str, Any]) -> list[str]:
    """Avisos de derechos de las fuentes usadas en el montaje frente a la difusión declarada."""
    out = []
    used = set(timeline.used_sources(p["timeline"]))
    dist = (p.get("use_declaration") or {}).get("distribution")
    for s in services.list_sources(p["id"]):
        if s["id"] not in used:
            continue
        lic = (s["origin"] or {}).get("license") or {}
        prov = s.get("provenance") or {}
        if lic.get("allows_commercial") is False and dist == "comercial":
            out.append(f"«{s['label']}» tiene licencia no comercial y ha declarado uso comercial.")
        if lic.get("share_alike"):
            out.append(f"«{s['label']}» exige publicar el resultado con la misma licencia ({lic.get('name')}).")
        if not prov.get("can_distribute") and dist != "privado":
            out.append(f"No ha confirmado permiso para difundir «{s['label']}».")
    if services.credits_required(p) and not p["timeline"].get("credits", {}).get("enabled", True):
        out.append("Alguna fuente exige atribución y ha desactivado los créditos en pantalla: se incluirán en los "
                   "metadatos y en el informe, pero asegúrese de citarlos también al publicar.")
    return out


@router.post("/projects/{pid}/estimate")
def estimate_render(pid: str, body: RenderIn, user=Depends(current_user)) -> dict[str, Any]:
    p = services.load_project(pid, user["id"])
    opts = render.RenderOptions(body.resolution, body.fps, body.codec, start=body.start, end=body.end)
    render.validate_options(opts)
    est = render.estimate(p["timeline"], p["aspect"], opts)
    est["h265_available"] = render.ffmpeg.has_encoder("libx265")
    est["rights_warnings"] = rights_warnings(p)
    est["credits"] = services.credits(p)
    est["disclaimer"] = watermark.DISCLAIMER
    return est


@router.post("/projects/{pid}/preview")
def preview_render(pid: str, body: RenderIn, user=Depends(current_user)) -> dict[str, Any]:
    p = services.load_project(pid, user["id"])
    services.require_declaration(p)
    total = timeline.output_duration(p["timeline"])
    start = body.start or 0.0
    end = body.end if body.end is not None else min(total, start + 15)
    job = queue.enqueue("render", user["id"], pid, {"kind": "preview", "resolution": "360p", "fps": 25,
                                                     "codec": "h264", "start": start, "end": end},
                        message="Generando vista previa")
    return {"job": job}


@router.post("/projects/{pid}/export")
def export(pid: str, body: RenderIn, user=Depends(current_user)) -> dict[str, Any]:
    p = services.load_project(pid, user["id"])
    services.require_declaration(p)
    if not p["timeline"]["video"]["clips"]:
        raise AppError("El montaje no tiene escenas.", code="linea_de_tiempo")
    if not body.ack_disclaimer:
        raise AppError("Debe leer y aceptar el aviso sobre contenido sintético y autorizaciones.",
                       code="aviso_pendiente", details={"disclaimer": watermark.DISCLAIMER})
    services.check_identities_still_valid(p["id"], user["id"])
    opts = render.RenderOptions(body.resolution, body.fps, body.codec)
    render.validate_options(opts)
    est = render.estimate(p["timeline"], p["aspect"], opts)
    job = queue.enqueue("render", user["id"], pid, {"kind": "export", "resolution": body.resolution,
                                                     "fps": body.fps, "codec": body.codec, "ack": True},
                        message="Exportando")
    return {"job": job, "estimate": est}


@router.get("/projects/{pid}/exports")
def list_exports(pid: str, user=Depends(current_user)) -> list[dict[str, Any]]:
    services.load_project(pid, user["id"])
    rows = db.all_("""SELECT id, size, duration, width, height, meta, created_at FROM assets
                      WHERE project_id=? AND kind='render' ORDER BY created_at DESC""", (pid,))
    for r in rows:
        r["meta"] = db.jload(r["meta"], {})
    return rows


@router.get("/system/lipsync")
def lipsync_status(user=Depends(current_user)) -> dict[str, Any]:
    return lipsync.status()
