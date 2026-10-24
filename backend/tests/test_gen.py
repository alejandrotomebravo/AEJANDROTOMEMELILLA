"""Generador gratuito y local: estilos, filtros, guion por reglas y animática con FFmpeg real."""
from __future__ import annotations

import subprocess
from pathlib import Path

import cv2
import numpy as np
import pytest

from app import policy
from app.errors import AppError, PolicyError
from app.gen import images, llm, styles, video

CAST = [{"name": "Ana", "identity_id": "x", "description": "pelo rizado"}, {"name": "Luis", "identity_id": None}]


def _photo(path: Path) -> Path:
    img = np.full((600, 480, 3), 200, np.uint8)
    cv2.circle(img, (240, 260), 120, (120, 160, 210), -1)
    cv2.imwrite(str(path), img)
    return path


# ------------------------------------------------------------------ estilos y filtros

def test_style_catalog():
    keys = [s["key"] for s in styles.catalog()]
    assert {"realista", "caricatura", "comic", "anime", "acuarela", "boceto", "pixel"} <= set(keys)
    with pytest.raises(AppError):
        styles.get("inventado")


@pytest.mark.parametrize("kind", ["comic", "anime", "boceto", "acuarela", "pixel", "realce"])
def test_local_filters_keep_shape(kind):
    img = np.random.default_rng(1).integers(0, 255, (128, 128, 3), dtype=np.uint8)
    out = images.apply_local_filter(img, kind)
    assert out.shape == img.shape and out.dtype == np.uint8


def test_local_portrait_and_keyframe(env, tmp_path):
    photo = _photo(tmp_path / "foto.jpg")
    info = images.portrait([photo], "caricatura", "", tmp_path / "retrato.jpg")
    assert info["provider"] == "local" and cv2.imread(str(tmp_path / "retrato.jpg")).shape == (768, 768, 3)
    cast = [{"name": "Ana", "portrait_path": str(tmp_path / "retrato.jpg")}]
    scene = {"title": "En la playa", "description": "Ana celebra con los brazos en alto", "characters": ["Ana"]}
    images.keyframe(scene, cast, "caricatura", "9:16", tmp_path / "kf.jpg")
    assert cv2.imread(str(tmp_path / "kf.jpg")).shape == (1280, 720, 3)
    images.watermark_image(tmp_path / "kf.jpg", tmp_path / "kf_prev.jpg")
    a, b = cv2.imread(str(tmp_path / "kf.jpg")), cv2.imread(str(tmp_path / "kf_prev.jpg"))
    assert np.abs(a[-60:, :300].astype(int) - b[-60:, :300].astype(int)).mean() > 10  # aviso visible abajo


# ------------------------------------------------------------------ guion

def test_basic_storyboard():
    b = llm.basic_storyboard("Ana llega a la playa. Luis le grita «¡Hemos ganado!». Los dos bailan.", CAST, 5)
    assert len(b["scenes"]) == 3
    assert b["scenes"][0]["characters"] == ["Ana"]
    assert b["scenes"][1]["dialogue"] == [{"character": "Luis", "line": "¡Hemos ganado!"}]
    assert b["scenes"][2]["characters"] == ["Ana", "Luis"]  # sin nombres: todo el reparto (≤2)


def test_normalize_cleans_storyboard():
    raw = {"scenes": [{"title": "x", "characters": ["Ana", "Famoso"], "duration_seconds": 40,
                       "dialogue": [{"character": "Ana", "line": " Hola "}, {"character": "Ana", "line": ""}]}] * 20}
    n = llm.normalize(raw, CAST, 6)
    assert len(n["scenes"]) == 6
    s = n["scenes"][0]
    assert s["characters"] == ["Ana"] and s["duration_seconds"] == 10 and s["dialogue"] == [{"character": "Ana", "line": "Hola"}]
    assert len({sc["id"] for sc in n["scenes"]}) == 6


def test_target_duration_spreads_over_scenes():
    b = llm.storyboard("Uno. Dos. Tres. Cuatro.", CAST, style_label="x", aspect="9:16", max_scenes=4, target_seconds=32)
    assert [sc["duration_seconds"] for sc in b["scenes"]] == [8, 8, 8, 8]


def test_app_is_free_no_paid_integrations():
    """Garantía de gratuidad: ningún módulo contiene integraciones con servicios de pago ni red de salida."""
    root = Path(__file__).resolve().parents[1] / "app"
    banned = ("anthropic", "fal.run", "fal_key", "elevenlabs", "api.openai", "replicate", "api_key", "urllib.request",
              "http.client", "requests.")
    for f in root.rglob("*.py"):
        text = f.read_text(encoding="utf-8").lower()
        for word in banned:
            assert word not in text, f"{f.name} contiene «{word}»"


def test_instructions_filter():
    with pytest.raises(PolicyError):
        policy.check_instructions("Haz un vídeo que parezca real del alcalde")
    policy.check_instructions("Ana y Luis celebran en la playa de Melilla, en estilo caricatura")


# ------------------------------------------------------------------ animática local (FFmpeg real)

@pytest.mark.parametrize("camera", ["Plano general con acercamiento", "paneo lateral", "alejamiento"])
def test_local_animatic(env, tmp_path, camera):
    kf = tmp_path / "kf.jpg"
    cv2.imwrite(str(kf), np.random.default_rng(2).integers(0, 255, (1280, 720, 3), dtype=np.uint8))
    info = video.animate({"duration_seconds": 4, "camera": camera}, kf, "comic", "9:16", tmp_path / "c.mp4")
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=width,height:format=duration",
                          "-of", "default=nw=1", str(tmp_path / "c.mp4")], capture_output=True, text=True).stdout
    assert info["seconds"] == 4 and "width=720" in out and "height=1280" in out
    assert abs(float(next(l for l in out.splitlines() if l.startswith("duration=")).split("=")[1]) - 4) < 0.2
