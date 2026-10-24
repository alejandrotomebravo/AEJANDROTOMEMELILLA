from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from .. import db, policy, services
from ..ai import asr, tts
from ..config import settings
from ..media import ffmpeg, provenance, render, watermark
from ..gen import images, llm, styles, video
from .deps import current_user

router = APIRouter(prefix="/api/system", tags=["sistema"])


@router.get("/health")
def health() -> dict[str, Any]:
    db.one("SELECT 1 AS ok")
    return {"ok": True, "version": services.APP_VERSION}


@router.get("/capabilities")
def capabilities(user=Depends(current_user)) -> dict[str, Any]:
    """Estado real de cada módulo: la interfaz desactiva lo que no está disponible."""
    fd = "yunet" if settings.yunet_model_path else "haar"
    return {
        "version": services.APP_VERSION,
        "face_detection": {"available": True, "detector": fd},
        "llm": llm.status(),
        "image": images.status(),
        "video": video.status(),
        "styles": styles.catalog(),
        "tts": {"voices": len(tts.list_voices()),
                "providers": sorted({v.provider for v in tts.list_voices()})},
        "asr": asr.status(),
        "c2pa": {"available": provenance.c2pa_available(),
                 "signed_with_own_certificate": bool(settings.c2pa_sign_cert and settings.c2pa_private_key)},
        "export": {"h264": ffmpeg.has_encoder("libx264"), "h265": ffmpeg.has_encoder("libx265"),
                   "resolutions": [r for r in render.RESOLUTIONS if r != "360p" and (r != "2160p" or settings.allow_4k)],
                   "fps": sorted(render.FPS_OPTIONS)},
        "limits": {"max_upload_mb": settings.max_upload_mb, "max_video_seconds": settings.max_video_seconds,
                   "max_dialogue_seconds": settings.max_dialogue_seconds, "allow_4k": settings.allow_4k,
                   "allow_minors": settings.allow_minors},
        "retention": {"project_days": settings.retention_days, "temp_hours": settings.temp_retention_hours},
        "watermark": {"base_text": watermark.BASE_TEXT, "labels": watermark.ALLOWED_LABELS,
                      "positions": watermark.POSITIONS, "size": [watermark.MIN_SIZE, watermark.MAX_SIZE],
                      "opacity": [watermark.MIN_OPACITY, watermark.MAX_OPACITY], "disclaimer": watermark.DISCLAIMER},
        "policy": {"purposes": policy.PURPOSES,
                   "confirmations": policy.REQUIRED_CONFIRMATIONS},
    }


@router.get("/usage")
def usage(user=Depends(current_user)) -> dict[str, Any]:
    rows = db.all_("""SELECT provider, unit, SUM(units) AS units, SUM(cost) AS cost, COUNT(*) AS n
                      FROM usage WHERE user_id=? GROUP BY provider, unit""", (user["id"],))
    return {"items": rows, "total_cost_usd": round(sum(r["cost"] or 0 for r in rows), 2)}


@router.get("/privacy/export")
def privacy_export(user=Depends(current_user)) -> dict[str, Any]:
    """Derecho de acceso: resumen de los datos que la aplicación guarda del usuario."""
    return {
        "cuenta": {"email": user["email"]},
        "proyectos": db.all_("SELECT id, name, created_at, updated_at FROM projects WHERE user_id=?", (user["id"],)),
        "identidades": db.all_("SELECT id, display_name, kind, consent_status, created_at FROM identities WHERE user_id=?",
                               (user["id"],)),
        "archivos": db.all_("SELECT kind, COUNT(*) AS n, SUM(size) AS bytes FROM assets WHERE user_id=? GROUP BY kind",
                            (user["id"],)),
        "registro_de_actividad": db.all_("SELECT action, project_id, created_at FROM audit WHERE user_id=? "
                                         "ORDER BY created_at DESC LIMIT 500", (user["id"],)),
        "entrenamiento_de_modelos": "La aplicación no utiliza sus archivos para entrenar modelos.",
    }
