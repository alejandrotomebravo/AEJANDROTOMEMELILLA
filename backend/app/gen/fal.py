"""Cliente de la cola de fal.ai (https://fal.ai), proveedor de modelos de imagen y vídeo.

Flujo documentado: ``POST {queue}/{modelo}`` con cabecera ``Authorization: Key …`` devuelve
``request_id``, ``status_url`` y ``response_url``; se consulta el estado hasta ``COMPLETED`` y
se recoge el resultado. Todas las llamadas pasan por ``app.net`` (validación anti-SSRF y
límites de tamaño), también la descarga del archivo generado.

Las imágenes de referencia se envían como *data URI* (base64), que los modelos de fal admiten
como alternativa a una URL pública; así no es necesario publicar fotos de personas.
"""
from __future__ import annotations

import base64
import mimetypes
import time
from pathlib import Path
from typing import Any, Callable

from .. import net
from ..config import settings
from ..errors import AppError, JobCancelled, ProviderUnavailable, RetryableError


def available() -> bool:
    return bool(settings.fal_key)


def _headers() -> dict[str, str]:
    if not settings.fal_key:
        raise ProviderUnavailable("Falta FAL_KEY: configure la clave de fal.ai en el servidor.")
    return {"Authorization": f"Key {settings.fal_key}"}


def data_uri(path: Path) -> str:
    mime = mimetypes.guess_type(path.name)[0] or "image/jpeg"
    return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode()}"


def run(model: str, arguments: dict[str, Any], *, checkpoint: dict[str, Any] | None = None,
        on_progress: Callable[[float], None] | None = None,
        should_cancel: Callable[[], bool] | None = None) -> dict[str, Any]:
    """Envía un trabajo a la cola y espera el resultado. Reanudable mediante ``checkpoint``."""
    checkpoint = checkpoint if checkpoint is not None else {}
    key = f"fal:{model}"
    req = checkpoint.get(key)
    if not req:
        res = net.get_json(f"{settings.fal_queue_base}/{model}", headers=_headers(), method="POST",
                           json_body=arguments)
        if not res.get("request_id"):
            raise AppError("Respuesta inesperada de fal.ai (sin identificador de trabajo).", code="fal")
        req = {"status_url": res.get("status_url") or f"{settings.fal_queue_base}/{model}/requests/{res['request_id']}/status",
               "response_url": res.get("response_url") or f"{settings.fal_queue_base}/{model}/requests/{res['request_id']}",
               "request_id": res["request_id"]}
        checkpoint[key] = req
    deadline = time.time() + settings.fal_timeout_seconds
    while True:
        if should_cancel and should_cancel():
            raise JobCancelled()
        st = net.get_json(req["status_url"], headers=_headers())
        status = (st.get("status") or "").upper()
        if status == "COMPLETED":
            if st.get("error"):
                checkpoint.pop(key, None)
                raise AppError(f"fal.ai no pudo generar el resultado: {st['error']}", code="fal")
            out = net.get_json(req["response_url"], headers=_headers())
            checkpoint.pop(key, None)
            return out
        if status in ("FAILED", "ERROR", "CANCELLED"):
            checkpoint.pop(key, None)
            raise AppError(f"fal.ai no pudo generar el resultado ({status}).", code="fal")
        if on_progress:
            on_progress(0.6 if status == "IN_PROGRESS" else 0.15)
        if time.time() > deadline:
            raise RetryableError("fal.ai tarda más de lo previsto; se volverá a consultar.")
        time.sleep(settings.fal_poll_seconds)


def first_image_url(result: dict[str, Any]) -> str:
    imgs = result.get("images") or ([result["image"]] if result.get("image") else [])
    if not imgs or not imgs[0].get("url"):
        raise AppError("fal.ai no devolvió ninguna imagen (puede haberla bloqueado su filtro de contenido).", code="fal")
    return imgs[0]["url"]


def video_url(result: dict[str, Any]) -> str:
    v = result.get("video")
    url = v.get("url") if isinstance(v, dict) else v
    if not url:
        raise AppError("fal.ai no devolvió ningún vídeo (puede haberlo bloqueado su filtro de contenido).", code="fal")
    return url


def download(url: str, dest: Path, max_mb: int = 300) -> Path:
    """Descarga un resultado. Para imágenes se usa la misma ruta segura que para vídeo."""
    if url.startswith("data:"):
        header, b64 = url.split(",", 1)
        dest.write_bytes(base64.b64decode(b64))
        return dest
    r = net.request(url)
    try:
        if r.status >= 400:
            raise RetryableError(f"No se pudo descargar el resultado de fal.ai ({r.status}).")
        data = r.read(max_mb * 1024 * 1024 + 1)
        if len(data) > max_mb * 1024 * 1024:
            raise AppError("El resultado generado es demasiado grande.", code="fal")
        if data[:2] == b"MZ" or data[:4] == b"\x7fELF" or data.lstrip()[:5].lower() == b"<html":
            raise AppError("El resultado descargado no es un archivo multimedia.", code="fal")
        dest.write_bytes(data)
        return dest
    finally:
        r.close()
