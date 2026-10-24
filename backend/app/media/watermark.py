"""Marca de agua obligatoria de contenido sintético.

La marca se valida y se aplica en el servidor en TODAS las salidas visibles
(vistas previas, revisiones y exportaciones de cualquier resolución). No
existe ningún parámetro de la API que permita desactivarla: sólo pueden
ajustarse posición, tamaño y opacidad dentro de márgenes que garantizan que
siga siendo legible.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ..config import settings
from ..errors import PolicyError

BASE_TEXT = "VÍDEO EDITADO CON INTELIGENCIA ARTIFICIAL"
ALLOWED_LABELS = ["PARODIA", "FICCIÓN", "DOBLAJE SINTÉTICO", "ROSTROS ALTERADOS CON IA"]
# Etiquetas que se añaden obligatoriamente según el procesamiento realizado.
FACES_LABEL = "ROSTROS ALTERADOS CON IA"
POSITIONS = ["top-left", "top-center", "top-right", "bottom-left", "bottom-center", "bottom-right"]

MIN_SIZE, MAX_SIZE, DEFAULT_SIZE = 0.022, 0.06, 0.03
MIN_OPACITY, MAX_OPACITY, DEFAULT_OPACITY = 0.6, 1.0, 0.85
MAX_CUSTOM = 80

DEFAULT_WATERMARK = {
    "position": "bottom-left",
    "size": DEFAULT_SIZE,
    "opacity": DEFAULT_OPACITY,
    "labels": [],
    "custom": "",
}


def validate(cfg: dict[str, Any] | None) -> dict[str, Any]:
    """Normaliza la configuración. Rechaza intentos de ocultar la marca."""
    cfg = dict(cfg or {})
    tampering = cfg.get("enabled") is False or any(
        k in cfg for k in ("disabled", "hidden", "text", "base_text"))
    if tampering:
        raise PolicyError(
            "La marca de agua de contenido sintético es obligatoria y su texto base no puede "
            "modificarse ni ocultarse."
        )
    cfg.pop("enabled", None)
    out = dict(DEFAULT_WATERMARK)
    pos = cfg.get("position", out["position"])
    if pos not in POSITIONS:
        raise PolicyError(f"Posición de la marca de agua no válida: {pos}")
    out["position"] = pos
    size = float(cfg.get("size", out["size"]))
    out["size"] = min(MAX_SIZE, max(MIN_SIZE, size))
    opacity = float(cfg.get("opacity", out["opacity"]))
    out["opacity"] = min(MAX_OPACITY, max(MIN_OPACITY, opacity))
    labels = cfg.get("labels") or []
    bad = [l for l in labels if l not in ALLOWED_LABELS]
    if bad:
        raise PolicyError(f"Etiquetas no reconocidas: {', '.join(bad)}")
    out["labels"] = [l for l in ALLOWED_LABELS if l in labels]
    custom = (cfg.get("custom") or "").strip()
    if len(custom) > MAX_CUSTOM:
        raise PolicyError(f"El aviso personalizado no puede superar {MAX_CUSTOM} caracteres.")
    out["custom"] = custom.replace("\n", " ")
    return out


def with_required(cfg: dict[str, Any], *, faces_altered: bool) -> dict[str, Any]:
    """Añade las etiquetas obligatorias (no eliminables) según las operaciones del proyecto."""
    cfg = validate(cfg)
    if faces_altered and FACES_LABEL not in cfg["labels"]:
        cfg["labels"] = [l for l in ALLOWED_LABELS if l in cfg["labels"] + [FACES_LABEL]]
    return cfg


def lines(cfg: dict[str, Any]) -> list[str]:
    result = [BASE_TEXT]
    extra = list(cfg.get("labels") or [])
    if cfg.get("custom"):
        extra.append(cfg["custom"])
    if extra:
        result.append(" · ".join(extra))
    return result


def filter_escape(value: str) -> str:
    """Escapa un valor para usarlo dentro de una opción de filtro de FFmpeg."""
    return value.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'").replace(",", "\\,") \
        .replace("[", "\\[").replace("]", "\\]").replace(";", "\\;")


def font_option() -> str:
    if settings.font_path and Path(settings.font_path).is_file():
        return f"fontfile={filter_escape(settings.font_path)}"
    return "font=Sans"


def drawtext_filters(cfg: dict[str, Any], workdir: Path, prefix: str = "wm") -> list[str]:
    """Devuelve filtros ``drawtext`` (uno por línea) para la marca de agua."""
    cfg = validate(cfg)
    texts = lines(cfg)
    pos = cfg["position"]
    op = cfg["opacity"]
    fsize = f"(min(w\\,h)*{cfg['size']:.4f})"
    margin = "(min(w\\,h)*0.03)"
    line_gap = f"({fsize}*1.45)"
    filters = []
    n = len(texts)
    for i, text in enumerate(texts):
        tf = workdir / f"{prefix}_{i}.txt"
        tf.write_text(text, encoding="utf-8")
        size_i = fsize if i == 0 else f"({fsize}*0.8)"
        if pos.endswith("left"):
            x = margin
        elif pos.endswith("right"):
            x = f"(w-text_w-{margin})"
        else:
            x = "((w-text_w)/2)"
        if pos.startswith("top"):
            y = f"({margin}+{i}*{line_gap})"
        else:
            y = f"(h-{margin}-text_h-{n - 1 - i}*{line_gap})"
        filters.append(
            "drawtext=" + ":".join([
                font_option(),
                f"textfile={filter_escape(str(tf))}",
                "expansion=none",
                f"fontsize={size_i}",
                f"fontcolor=white@{op:.2f}",
                "box=1",
                f"boxcolor=black@{op * 0.55:.2f}",
                f"boxborderw={max(4, int(cfg['size'] * 200))}",
                "borderw=1",
                f"bordercolor=black@{op:.2f}",
                f"x={x}",
                f"y={y}",
            ])
        )
    return filters


DISCLAIMER = (
    "La marca de agua identifica el vídeo como contenido editado o generado con inteligencia "
    "artificial. NO sustituye a las autorizaciones necesarias de derechos de autor, derechos de "
    "imagen o derechos sobre la voz de las personas que aparecen, ni a la licencia del material "
    "audiovisual de terceros. Usted sigue siendo responsable de disponer de ellas."
)
