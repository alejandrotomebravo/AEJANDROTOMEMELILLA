from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from .. import db, services
from ..errors import AppError, NotFound
from ..jobs import queue
from ..media import render, timeline, watermark
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


# ------------------------------------------------------------ render

class RenderIn(BaseModel):
    resolution: str = "1080p"
    fps: int = 30
    codec: str = "h264"
    start: float | None = None
    end: float | None = None
    ack_disclaimer: bool = False


@router.post("/projects/{pid}/estimate")
def estimate_render(pid: str, body: RenderIn, user=Depends(current_user)) -> dict[str, Any]:
    p = services.load_project(pid, user["id"])
    opts = render.RenderOptions(body.resolution, body.fps, body.codec, start=body.start, end=body.end)
    render.validate_options(opts)
    est = render.estimate(p["timeline"], p["aspect"], opts)
    est["h265_available"] = render.ffmpeg.has_encoder("libx265")
    pending = [sc["title"] for sc in services.scenes(p) if not sc.get("clip")]
    est["warnings"] = ([f"Escenas sin clip generado (no se incluirán): {', '.join(pending)}."] if pending else [])
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
    services.require_cast_usable(p, user["id"])
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
