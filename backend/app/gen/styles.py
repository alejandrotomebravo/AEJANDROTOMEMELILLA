"""Catálogo de estilos visuales de los personajes y de las escenas.

``prompt``: descripción que se añade a los prompts de los modelos de IA.
``local``: filtro que se aplica en el modo local gratuito (sólo aproxima el estilo).
"""
from __future__ import annotations

from ..errors import AppError

STYLES: dict[str, dict[str, str]] = {
    "realista": {
        "label": "Realista",
        "prompt": "photorealistic, natural lighting, realistic skin texture, cinematic photography",
        "local": "realce",
    },
    "caricatura": {
        "label": "Caricatura",
        "prompt": ("hand-drawn caricature, big head on a small body, exaggerated but recognizable facial features, "
                   "bold ink outlines, flat vibrant colors, humorous editorial cartoon style"),
        "local": "comic",
    },
    "comic": {
        "label": "Cómic",
        "prompt": "comic book illustration, strong black ink lines, halftone shading, saturated colors",
        "local": "comic",
    },
    "anime": {
        "label": "Anime",
        "prompt": "anime illustration, clean cel shading, expressive eyes, soft gradients",
        "local": "anime",
    },
    "animacion3d": {
        "label": "Animación 3D",
        "prompt": "3D animated feature film style, soft global illumination, stylized proportions, smooth materials",
        "local": "realce",
    },
    "acuarela": {
        "label": "Acuarela",
        "prompt": "watercolor painting, soft washes, visible paper texture, gentle colors",
        "local": "acuarela",
    },
    "boceto": {
        "label": "Boceto a lápiz",
        "prompt": "pencil sketch, graphite shading, hand-drawn lines on white paper",
        "local": "boceto",
    },
    "pixel": {
        "label": "Pixel art",
        "prompt": "16-bit pixel art, limited color palette, crisp pixels",
        "local": "pixel",
    },
    "plastilina": {
        "label": "Plastilina",
        "prompt": "claymation stop-motion style, plasticine textures, handmade look, soft studio light",
        "local": "realce",
    },
}

DEFAULT_STYLE = "caricatura"


def get(key: str) -> dict[str, str]:
    if key not in STYLES:
        raise AppError(f"Estilo desconocido: {key}", code="estilo")
    return STYLES[key]


def catalog() -> list[dict[str, str]]:
    return [{"key": k, "label": v["label"]} for k, v in STYLES.items()]
