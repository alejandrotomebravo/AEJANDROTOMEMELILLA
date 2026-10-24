"""Metadatos de procedencia.

* Siempre: etiquetas MP4 (``comment``/``description``) y un manifiesto JSON
  adjunto (no firmado) con las operaciones sintéticas realizadas.
* Si está instalado ``c2patool`` (proyecto Content Authenticity Initiative,
  licencias MIT/Apache-2.0): se incrusta un manifiesto C2PA firmado. Sin
  certificado propio, c2patool usa un certificado de pruebas que los
  verificadores marcarán como no confiable; en producción hay que configurar
  C2PA_SIGN_CERT y C2PA_PRIVATE_KEY.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

from ..config import settings

IPTC_COMPOSITE = "http://cv.iptc.org/newscodes/digitalsourcetype/compositeWithTrainedAlgorithmicMedia"
IPTC_TRAINED = "http://cv.iptc.org/newscodes/digitalsourcetype/trainedAlgorithmicMedia"


def mp4_metadata(project_name: str, operations: list[str]) -> dict[str, str]:
    ops = ", ".join(operations) if operations else "edición"
    return {
        "title": project_name[:120],
        "comment": f"Contenido sintético: vídeo alterado con inteligencia artificial ({ops}).",
        "description": "VÍDEO EDITADO CON INTELIGENCIA ARTIFICIAL",
        "encoder": "Estudio IA de vídeo",
    }


def build_manifest(project: dict[str, Any], operations: list[str], app_version: str) -> dict[str, Any]:
    actions = [{
        "action": "c2pa.edited",
        "digitalSourceType": IPTC_COMPOSITE,
        "softwareAgent": {"name": "Estudio IA de vídeo", "version": app_version},
        "description": "Vídeo alterado mediante IA: " + (", ".join(operations) or "edición"),
    }]
    manifest: dict[str, Any] = {
        "claim_generator_info": [{"name": "Estudio IA de vídeo", "version": app_version}],
        "title": project["name"][:120],
        "assertions": [
            {"label": "c2pa.actions", "data": {"actions": actions}},
        ],
    }
    if settings.c2pa_sign_cert and settings.c2pa_private_key:
        manifest["alg"] = settings.c2pa_alg
        manifest["sign_cert"] = settings.c2pa_sign_cert
        manifest["private_key"] = settings.c2pa_private_key
    return manifest


def write_sidecar(path: Path, project: dict[str, Any], operations: list[str], app_version: str,
                  extra: dict[str, Any]) -> Path:
    data = {
        "aviso": "VÍDEO EDITADO CON INTELIGENCIA ARTIFICIAL",
        "operaciones": operations,
        "digitalSourceType": IPTC_COMPOSITE,
        "proyecto": project["name"],
        "generador": {"nombre": "Estudio IA de vídeo", "version": app_version},
        "firmado": False,
        **extra,
    }
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def c2pa_available() -> bool:
    return bool(settings.c2pa_tool) and shutil.which(settings.c2pa_tool) is not None


def embed_c2pa(video: Path, manifest: dict[str, Any], workdir: Path) -> dict[str, Any]:
    """Firma ``video`` in situ. Devuelve el estado (nunca lanza: la marca visible ya existe)."""
    if not c2pa_available():
        return {"c2pa": False, "motivo": "c2patool no está instalado en el servidor."}
    mpath = workdir / "c2pa_manifest.json"
    mpath.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    signed = workdir / f"signed_{video.name}"
    proc = subprocess.run([settings.c2pa_tool, str(video), "-m", str(mpath), "-o", str(signed), "-f"],
                          capture_output=True, text=True, timeout=600)
    if proc.returncode != 0 or not signed.exists():
        return {"c2pa": False, "motivo": "c2patool devolvió un error al firmar.",
                "detalle": proc.stderr.strip()[-300:]}
    signed.replace(video)
    test_cert = not (settings.c2pa_sign_cert and settings.c2pa_private_key)
    return {"c2pa": True, "certificado_de_pruebas": test_cert}
