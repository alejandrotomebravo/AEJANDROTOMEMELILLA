"""Clips de vídeo de cada escena a partir de su fotograma clave.

* ``fal``: modelo imagen→vídeo vía fal.ai (por defecto Kling 2.1 Standard, configurable con
  FAL_VIDEO_MODEL). De pago por segundo generado.
* ``local``: «animática» gratuita con FFmpeg: movimiento de cámara (acercamiento, alejamiento o
  desplazamiento) sobre el fotograma clave. No es generación de movimiento por IA.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from ..config import settings
from ..errors import ProviderUnavailable
from ..media import ffmpeg
from . import fal, styles

NEGATIVE = "blurry, distorted face, extra limbs, deformed hands, text, watermark, logo"


def provider() -> str:
    p = settings.video_provider
    if p == "fal" or (p == "auto" and fal.available()):
        return "fal"
    return "local"


def status() -> dict[str, Any]:
    p = provider()
    if settings.video_provider == "fal" and not fal.available():
        return {"provider": "fal", "available": False, "reason": "Falta FAL_KEY."}
    if p == "fal":
        return {"provider": "fal", "available": True, "paid": True, "model": settings.fal_video_model,
                "cost_per_second": settings.fal_video_cost_per_second, "durations": allowed_durations()}
    return {"provider": "local", "available": True, "paid": False, "durations": [3, 4, 5, 6, 8, 10],
            "note": "Modo local: animática con movimiento de cámara sobre el fotograma (sin IA de vídeo)."}


def allowed_durations() -> list[int]:
    m = settings.fal_video_model
    if "kling" in m:
        return [5, 10]
    return [5]


def clip_duration(requested: float) -> int:
    opts = allowed_durations() if provider() == "fal" else [3, 4, 5, 6, 8, 10]
    return min(opts, key=lambda d: (abs(d - requested), d))


def estimate(seconds: float) -> dict[str, Any]:
    d = clip_duration(seconds)
    if provider() == "fal":
        return {"provider": "fal", "seconds": d, "cost_usd": round(d * settings.fal_video_cost_per_second, 2),
                "estimated_wait_seconds": 120 + d * 20}
    return {"provider": "local", "seconds": d, "cost_usd": 0.0, "estimated_wait_seconds": 5 + d}


def _fal_arguments(prompt: str, image: Path, seconds: int) -> dict[str, Any]:
    m = settings.fal_video_model
    args: dict[str, Any] = {"prompt": prompt, "image_url": fal.data_uri(image)}
    if "kling" in m:
        args |= {"duration": str(seconds), "negative_prompt": NEGATIVE}
    elif "wan" in m:
        args |= {"negative_prompt": NEGATIVE}
    return args


def animate(scene: dict[str, Any], keyframe: Path, style_key: str, aspect: str, out: Path, *,
            prov: str | None = None, checkpoint: dict | None = None, on_progress=None,
            should_cancel=None) -> dict[str, Any]:
    prov = prov or provider()
    seconds = clip_duration(float(scene.get("duration_seconds") or 5))
    if prov == "fal":
        prompt = (f"{scene.get('video_prompt') or scene.get('description', '')} Camera: {scene.get('camera', '')}. "
                  f"Keep every character's face consistent with the first frame. Style: {styles.get(style_key)['prompt']}.")
        res = fal.run(settings.fal_video_model, _fal_arguments(prompt, keyframe, seconds), checkpoint=checkpoint,
                      on_progress=on_progress, should_cancel=should_cancel)
        raw = out.with_suffix(".raw.mp4")
        fal.download(fal.video_url(res), raw)
        ffmpeg.normalize_video(raw, out, max_height=1080)
        raw.unlink(missing_ok=True)
        return {"provider": "fal", "model": settings.fal_video_model, "prompt": prompt, "seconds": seconds,
                "cost_usd": round(seconds * settings.fal_video_cost_per_second, 2)}
    _animatic(keyframe, out, aspect, seconds, scene.get("camera", ""))
    return {"provider": "local", "seconds": seconds, "cost_usd": 0.0}


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


def require_available() -> None:
    st = status()
    if not st["available"]:
        raise ProviderUnavailable(st["reason"])
