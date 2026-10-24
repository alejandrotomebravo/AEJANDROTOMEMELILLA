"""Generación de imágenes, gratuita y local: retratos estilizados de los personajes y fotogramas clave.

Recorte del rostro con filtros de OpenCV (cómic, boceto, acuarela, pixel art…) y fotogramas compuestos
(retratos sobre un fondo con el título de la escena). No es IA generativa y la interfaz lo indica; el
proveedor es intercambiable para conectar en el futuro un modelo abierto en una GPU propia.
"""
from __future__ import annotations

import hashlib
import textwrap
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from ..ai import face_detect
from ..config import settings
from ..errors import AppError
from . import styles

FONT_BOLD = settings.font_path


def provider() -> str:
    return "local"


def status() -> dict[str, Any]:
    return {"provider": "local", "available": True,
            "note": "Filtros y composición locales, gratuitos (sin IA generativa)."}


# ------------------------------------------------------------------ retratos

def portrait(photos: list[Path], style_key: str, description: str, out: Path, *, prov: str | None = None,
             checkpoint: dict | None = None, on_progress=None, should_cancel=None) -> dict[str, Any]:
    style = styles.get(style_key)
    if not photos:
        raise AppError("El personaje no tiene fotografías de referencia.", code="sin_fotos")
    img = cv2.imread(str(photos[0]))
    if img is None:
        raise AppError("No se pudo leer la fotografía.", code="foto")
    crop = _face_crop(img)
    styled = apply_local_filter(crop, style["local"])
    cv2.imwrite(str(out), styled, [cv2.IMWRITE_JPEG_QUALITY, 92])
    return {"provider": "local", "filter": style["local"]}


def fictional_portrait(name: str, description: str, style_key: str, out: Path, *, prov: str | None = None,
                       checkpoint: dict | None = None, on_progress=None, should_cancel=None) -> dict[str, Any]:
    """Personaje ficticio (sin persona real): sólo descripción."""
    style = styles.get(style_key)
    canvas = Image.new("RGB", (768, 768), _color(name))
    d = ImageDraw.Draw(canvas)
    d.ellipse([234, 120, 534, 460], fill=(240, 220, 200), outline=(40, 40, 40), width=6)
    d.rounded_rectangle([164, 470, 604, 768], radius=120, fill=_color(name + "ropa"), outline=(40, 40, 40), width=6)
    _text(d, (384, 60), name, 48, center=True)
    canvas.save(out, quality=92)
    return {"provider": "local"}


# ------------------------------------------------------------------ fotogramas clave

def keyframe(scene: dict[str, Any], cast: list[dict[str, Any]], style_key: str, aspect: str, out: Path, *,
             prov: str | None = None, checkpoint: dict | None = None, on_progress=None,
             should_cancel=None) -> dict[str, Any]:
    style = styles.get(style_key)
    present = [c for c in cast if c["name"] in scene.get("characters", []) and c.get("portrait_path")]
    from ..media.render import output_size
    w, h = output_size(aspect, "720p")
    canvas = _gradient(w, h, scene.get("setting") or scene.get("title", ""))
    d = ImageDraw.Draw(canvas)
    margin = int(min(w, h) * 0.06)
    title_size = int(min(w, h) * 0.07)
    _text(d, (w // 2, margin), scene.get("title", ""), title_size, center=True)
    body = textwrap.fill(scene.get("description", ""), width=max(20, int(w / (min(w, h) * 0.022))))
    _text(d, (w // 2, margin + int(title_size * 1.6)), body, int(min(w, h) * 0.035), center=True)
    if present:
        size = int(min(w / (len(present) + 0.5), h * 0.45))
        gap = (w - size * len(present)) // (len(present) + 1)
        y = h - size - margin
        for i, c in enumerate(present):
            p = Image.open(c["portrait_path"]).convert("RGB").resize((size, size))
            mask = Image.new("L", (size, size), 0)
            ImageDraw.Draw(mask).ellipse([0, 0, size, size], fill=255)
            x = gap + i * (size + gap)
            canvas.paste(p, (x, y), mask)
            _text(d, (x + size // 2, y - int(size * 0.14)), c["name"], int(size * 0.1), center=True)
    canvas.save(out, quality=92)
    return {"provider": "local"}


# ------------------------------------------------------------------ utilidades

def _face_crop(img: np.ndarray) -> np.ndarray:
    faces = face_detect.FaceDetector().detect(img)
    h, w = img.shape[:2]
    if faces:
        f = max(faces, key=lambda d: d.w * d.h)
        cx, cy = f.x + f.w / 2, f.y + f.h * 0.62
        side = int(max(f.w, f.h) * 2.4)
    else:
        cx, cy, side = w / 2, h / 2, min(w, h)
    side = min(side, w, h)
    x0 = int(min(max(0, cx - side / 2), w - side))
    y0 = int(min(max(0, cy - side / 2), h - side))
    return cv2.resize(img[y0:y0 + side, x0:x0 + side], (768, 768), interpolation=cv2.INTER_AREA)


def apply_local_filter(img: np.ndarray, kind: str) -> np.ndarray:
    if kind == "comic" or kind == "anime":
        k = 7 if kind == "comic" else 5
        color = img
        for _ in range(2):
            color = cv2.bilateralFilter(color, 9, 75, 75)
        z = color.reshape((-1, 3)).astype(np.float32)
        _, labels, centers = cv2.kmeans(z, 10 if kind == "comic" else 14, None,
                                        (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 10, 1.0), 2,
                                        cv2.KMEANS_PP_CENTERS)
        quant = centers.astype(np.uint8)[labels.flatten()].reshape(img.shape)
        gray = cv2.medianBlur(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), k)
        edges = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY, 9, 4)
        return cv2.bitwise_and(quant, quant, mask=edges)
    if kind == "boceto":
        g, _ = cv2.pencilSketch(img, sigma_s=60, sigma_r=0.07, shade_factor=0.05)
        return cv2.cvtColor(g, cv2.COLOR_GRAY2BGR)
    if kind == "acuarela":
        return cv2.stylization(img, sigma_s=60, sigma_r=0.45)
    if kind == "pixel":
        small = cv2.resize(img, (64, 64), interpolation=cv2.INTER_AREA)
        z = small.reshape((-1, 3)).astype(np.float32)
        _, labels, centers = cv2.kmeans(z, 16, None, (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 10, 1.0),
                                        2, cv2.KMEANS_PP_CENTERS)
        small = centers.astype(np.uint8)[labels.flatten()].reshape(small.shape)
        return cv2.resize(small, (img.shape[1], img.shape[0]), interpolation=cv2.INTER_NEAREST)
    return cv2.detailEnhance(img, sigma_s=10, sigma_r=0.15)


def _color(seed: str) -> tuple[int, int, int]:
    h = hashlib.sha256(seed.encode()).digest()
    return (60 + h[0] % 150, 60 + h[1] % 150, 60 + h[2] % 150)


def _gradient(w: int, h: int, seed: str) -> Image.Image:
    a, b = np.array(_color(seed)), np.array(_color(seed + "2")) // 2
    t = np.linspace(0, 1, h)[:, None, None]
    arr = (a * (1 - t) + b * t).astype(np.uint8) * np.ones((1, w, 1), dtype=np.uint8)
    return Image.fromarray(arr, "RGB")


def _font(size: int) -> ImageFont.ImageFont:
    try:
        return ImageFont.truetype(FONT_BOLD, size) if FONT_BOLD else ImageFont.load_default()
    except OSError:
        return ImageFont.load_default()


def _text(d: ImageDraw.ImageDraw, xy: tuple[int, int], text: str, size: int, center: bool = False) -> None:
    font = _font(size)
    kw = {"anchor": "ma"} if center else {}
    d.multiline_text(xy, text, font=font, fill=(255, 255, 255), stroke_width=max(1, size // 14),
                     stroke_fill=(0, 0, 0), align="center", **kw)


def watermark_image(src: Path, dst: Path, text: str = "IMAGEN GENERADA CON IA") -> None:
    """Versión con aviso visible de las imágenes generadas (es la única que se sirve al navegador)."""
    with Image.open(src) as im:
        im = im.convert("RGB")
        w, h = im.size
        d = ImageDraw.Draw(im, "RGBA")
        size = max(12, int(min(w, h) * 0.035))
        font = _font(size)
        pad = size // 2
        box = d.textbbox((0, 0), text, font=font)
        tw, th = box[2] - box[0], box[3] - box[1]
        x, y = pad, h - th - pad * 3
        d.rectangle([x - pad // 2, y - pad // 2, x + tw + pad, y + th + pad], fill=(0, 0, 0, 150))
        d.text((x, y), text, font=font, fill=(255, 255, 255, 235))
        im.save(dst, quality=90)
