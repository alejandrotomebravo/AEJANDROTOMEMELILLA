"""Reconocimiento de voz para subtítulos automáticos.

Proveedor: ``faster_whisper`` (paquete faster-whisper, MIT; modelos Whisper de
OpenAI, MIT). Opcional: si no está instalado, los subtítulos se generan a
partir del texto del diálogo o se escriben a mano.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ..config import settings
from ..errors import ProviderUnavailable

_model = None


def status() -> dict[str, Any]:
    if settings.asr_provider != "faster_whisper":
        return {"available": False, "reason": "Reconocimiento de voz no configurado (ASR_PROVIDER)."}
    try:
        import faster_whisper  # noqa: F401
    except Exception:
        return {"available": False, "reason": "El paquete «faster-whisper» no está instalado."}
    return {"available": True, "provider": "faster_whisper", "model": settings.whisper_model}


def transcribe(audio: Path, language: str | None = "es") -> list[dict[str, Any]]:
    global _model
    st = status()
    if not st["available"]:
        raise ProviderUnavailable(st["reason"])
    from faster_whisper import WhisperModel

    if _model is None:
        _model = WhisperModel(settings.whisper_model, device=settings.whisper_device,
                              compute_type="int8" if settings.whisper_device == "cpu" else "float16")
    segments, _info = _model.transcribe(str(audio), language=language, vad_filter=True)
    return [{"start": round(s.start, 3), "end": round(s.end, 3), "text": s.text.strip()}
            for s in segments if s.text.strip()]
