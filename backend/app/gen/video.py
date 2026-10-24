"""Clips de vídeo de cada escena a partir de su fotograma clave, gratis y en local.

«Animática» con FFmpeg: movimiento de cámara (acercamiento, alejamiento o desplazamiento) sobre el
fotograma clave. No es generación de movimiento por IA; el proveedor es intercambiable para conectar
en el futuro un modelo abierto de imagen→vídeo en una GPU propia.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from ..config import settings
from ..media import ffmpeg

DURATIONS = [3, 4, 5, 6, 8, 10]


def provider() -> str:
    return "local"


def status() -> dict[str, Any]:
    return {"provider": "local", "available": True, "durations": DURATIONS,
            "note": "Animática local con movimiento de cámara sobre el fotograma (sin IA de vídeo)."}


def clip_duration(requested: float) -> int:
    return min(DURATIONS, key=lambda d: (abs(d - requested), d))


def estimate(seconds: float) -> dict[str, Any]:
    d = clip_duration(seconds)
    return {"provider": "local", "seconds": d, "estimated_wait_seconds": 5 + d}


def animate(scene: dict[str, Any], keyframe: Path, style_key: str, aspect: str, out: Path, *,
            checkpoint: dict | None = None, on_progress=None, should_cancel=None) -> dict[str, Any]:
    seconds = clip_duration(float(scene.get("duration_seconds") or 5))
    _animatic(keyframe, out, aspect, seconds, scene.get("camera", ""))
    return {"provider": "local", "seconds": seconds}


def _animatic(image: Path, out: Path, aspect: str, seconds: int, camera: str) -> None:
    from ..media.render import output_size
    w, h = output_size(aspect, "720p")
    fps = 30
    frames = seconds * fps
    cam = camera.lower()
    if re.search(r"alej|zoom out|abre", cam):
        z = f"max(1.25-0.25*on/{frames},1.0)"
        x, y = "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"
    elif re.search(r"pane|desplaz|travel|lateral|pan", cam):
        z = "1.2"
        x, y = f"(iw-iw/zoom)*on/{frames}", "ih/2-(ih/zoom/2)"
    else:  # acercamiento suave por defecto
        z = f"min(1.0+0.25*on/{frames},1.25)"
        x, y = "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"
    vf = (f"scale={w * 2}:{h * 2}:force_original_aspect_ratio=increase,crop={w * 2}:{h * 2},"
          f"zoompan=z='{z}':x='{x}':y='{y}':d={frames}:s={w}x{h}:fps={fps},format=yuv420p")
    ffmpeg.run([settings.ffmpeg, "-y", "-loop", "1", "-i", str(image), "-f", "lavfi", "-i",
                "anullsrc=channel_layout=stereo:sample_rate=48000", "-vf", vf, "-t", str(seconds),
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-c:a", "aac", "-shortest",
                "-movflags", "+faststart", str(out)])
