"""Sincronización labial.

Proveedores:

* ``sync_so`` — API comercial de Sync Labs (https://api.sync.so/v2). Se envían
  URL firmadas temporales del fragmento de vídeo y del audio, por lo que el
  servidor debe ser accesible desde Internet (PUBLIC_BASE_URL). De pago por
  segundo procesado.
* ``command`` — ejecuta un modelo local instalado por el administrador
  (Wav2Lip, LatentSync, MuseTalk…) mediante una plantilla de orden con los
  marcadores ``{video}``, ``{audio}`` y ``{output}``. Cada modelo tiene su
  propia licencia (p. ej. Wav2Lip: sólo uso no comercial/investigación) y sus
  requisitos de GPU; se documentan en docs/ARQUITECTURA.md.

La aplicación procesa siempre fragmentos acotados (el tramo del diálogo), no
el vídeo completo, para reducir coste y permitir repetir sólo lo necesario.
"""
from __future__ import annotations

import json
import shlex
import subprocess
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any, Callable, Protocol

from ..config import settings
from ..errors import AppError, JobCancelled, ProviderUnavailable, RetryableError


class LipsyncProvider(Protocol):
    name: str
    paid: bool

    def run(self, video: Path, audio: Path, output: Path, *, publish: Callable[[Path], str],
            on_progress: Callable[[float], None] | None,
            should_cancel: Callable[[], bool] | None, checkpoint: dict[str, Any]) -> dict[str, Any]: ...


def status() -> dict[str, Any]:
    p = settings.lipsync_provider
    if p == "none":
        return {"available": False, "provider": None,
                "reason": "No hay motor de sincronización labial configurado (LIPSYNC_PROVIDER)."}
    if p == "sync_so":
        if not settings.sync_api_key:
            return {"available": False, "provider": p, "reason": "Falta SYNC_API_KEY."}
        if not settings.public_base_url:
            return {"available": False, "provider": p,
                    "reason": "sync.so necesita descargar los archivos: configure PUBLIC_BASE_URL con una URL "
                              "HTTPS accesible desde Internet."}
        return {"available": True, "provider": p, "paid": True, "model": settings.sync_model,
                "cost_per_second": settings.sync_cost_per_second,
                "seconds_per_second": settings.sync_seconds_per_second}
    if p == "command":
        if not settings.lipsync_command or "{output}" not in settings.lipsync_command:
            return {"available": False, "provider": p,
                    "reason": "LIPSYNC_COMMAND debe incluir los marcadores {video}, {audio} y {output}."}
        return {"available": True, "provider": p, "paid": False,
                "license": settings.lipsync_command_license or "Revise la licencia del modelo local instalado.",
                "seconds_per_second": settings.lipsync_command_seconds_per_second}
    return {"available": False, "provider": p, "reason": f"Proveedor desconocido: {p}"}


def get_provider() -> LipsyncProvider:
    st = status()
    if not st["available"]:
        raise ProviderUnavailable(st["reason"])
    if settings.lipsync_provider == "sync_so":
        return SyncSoProvider()
    return CommandProvider()


def estimate(seconds: float) -> dict[str, Any]:
    st = status()
    if not st["available"]:
        return {"available": False, "reason": st["reason"]}
    sps = st.get("seconds_per_second", 5.0)
    cost = round(seconds * st.get("cost_per_second", 0.0), 2) if st.get("paid") else 0.0
    return {"available": True, "provider": st["provider"], "paid": bool(st.get("paid")),
            "estimated_seconds": round(seconds * sps + 15), "estimated_cost_usd": cost}


class CommandProvider:
    name = "command"
    paid = False

    def run(self, video, audio, output, *, publish, on_progress=None, should_cancel=None,
            checkpoint=None) -> dict[str, Any]:
        parts = shlex.split(settings.lipsync_command)
        args = [p.replace("{video}", str(video)).replace("{audio}", str(audio)).replace("{output}", str(output))
                for p in parts]
        proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        started = time.time()
        while proc.poll() is None:
            if should_cancel and should_cancel():
                proc.kill()
                raise JobCancelled()
            if on_progress:
                # Los modelos locales no informan de su avance de forma estándar: estimación por tiempo.
                expected = max(10.0, settings.lipsync_command_seconds_per_second * 5)
                on_progress(min(0.95, (time.time() - started) / expected))
            time.sleep(1)
        _, err = proc.communicate()
        if proc.returncode != 0 or not output.exists() or output.stat().st_size == 0:
            tail = "\n".join((err or "").strip().splitlines()[-5:])
            raise AppError(f"El modelo local de sincronización labial falló. {tail}", code="lipsync_local")
        return {"provider": "command"}


class SyncSoProvider:
    name = "sync_so"
    paid = True

    def _request(self, method: str, path: str, body: dict | None = None,
                 headers: dict[str, str] | None = None) -> dict[str, Any]:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(f"{settings.sync_api_base}{path}", data=data, method=method)
        req.add_header("x-api-key", settings.sync_api_key)
        req.add_header("Content-Type", "application/json")
        for k, v in (headers or {}).items():
            req.add_header(k, v)
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return json.loads(resp.read().decode() or "{}")
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="ignore")[:300]
            if e.code in (429, 500, 502, 503, 504):
                raise RetryableError(f"sync.so no disponible temporalmente ({e.code}).")
            raise AppError(f"sync.so rechazó la solicitud ({e.code}): {detail}", code="lipsync_remoto")
        except urllib.error.URLError as e:
            raise RetryableError(f"No se pudo contactar con sync.so: {e.reason}")

    def run(self, video, audio, output, *, publish, on_progress=None, should_cancel=None,
            checkpoint=None) -> dict[str, Any]:
        checkpoint = checkpoint if checkpoint is not None else {}
        gen_id = checkpoint.get("sync_generation_id")
        if not gen_id:
            # La clave de idempotencia evita pagar dos veces si se reintenta tras un corte de red.
            idem = checkpoint.setdefault("sync_idempotency_key", uuid.uuid4().hex)
            body = {
                "model": settings.sync_model,
                "input": [{"type": "video", "url": publish(video)}, {"type": "audio", "url": publish(audio)}],
                "options": {"sync_mode": "cut_off"},
            }
            res = self._request("POST", "/generate", body, {"Idempotency-Key": idem})
            gen_id = res.get("id")
            if not gen_id:
                raise AppError("Respuesta inesperada de sync.so (sin identificador).", code="lipsync_remoto")
            checkpoint["sync_generation_id"] = gen_id
        deadline = time.time() + settings.sync_timeout_seconds
        while True:
            if should_cancel and should_cancel():
                raise JobCancelled()
            res = self._request("GET", f"/generate/{gen_id}")
            st = (res.get("status") or "").upper()
            if st == "COMPLETED":
                url = res.get("outputUrl") or res.get("output_url")
                if not url:
                    raise AppError("sync.so completó el trabajo pero no devolvió el vídeo.", code="lipsync_remoto")
                self._download(url, output)
                return {"provider": "sync_so", "generation_id": gen_id, "model": settings.sync_model}
            if st in ("FAILED", "REJECTED", "CANCELED", "CANCELLED"):
                checkpoint.pop("sync_generation_id", None)
                raise AppError(f"sync.so no pudo procesar el fragmento: {res.get('error') or st}",
                               code="lipsync_remoto")
            if on_progress:
                on_progress(0.5 if st == "PROCESSING" else 0.2)
            if time.time() > deadline:
                raise RetryableError("sync.so tarda más de lo previsto; se reintentará la consulta.")
            time.sleep(settings.sync_poll_seconds)

    @staticmethod
    def _download(url: str, output: Path) -> None:
        try:
            with urllib.request.urlopen(url, timeout=300) as resp, open(output, "wb") as f:
                while True:
                    b = resp.read(1024 * 1024)
                    if not b:
                        break
                    f.write(b)
        except urllib.error.URLError as e:
            raise RetryableError(f"No se pudo descargar el resultado de sync.so: {e}")
