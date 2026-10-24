"""Generador: estilos, filtros locales, guion (básico y con Claude simulado), cliente de fal.ai
simulado y animática local con FFmpeg real.

Las API de pago (Anthropic, fal.ai) se sustituyen por dobles de prueba explícitos: el entorno
de pruebas no tiene claves ni acceso a esos servicios.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from app import net, policy
from app.errors import AppError, JobCancelled, PolicyError
from app.gen import fal, images, llm, styles, video

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
    info = images.portrait([photo], "caricatura", "", tmp_path / "retrato.jpg", prov="local")
    assert info["provider"] == "local" and cv2.imread(str(tmp_path / "retrato.jpg")).shape == (768, 768, 3)
    cast = [{"name": "Ana", "portrait_path": str(tmp_path / "retrato.jpg")}]
    scene = {"title": "En la playa", "description": "Ana celebra con los brazos en alto", "characters": ["Ana"]}
    images.keyframe(scene, cast, "caricatura", "9:16", tmp_path / "kf.jpg", prov="local")
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


class _FakeClaude:
    def __init__(self, payload=None, stop_reason="end_turn"):
        self.payload, self.stop_reason, self.calls = payload, stop_reason, []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **kw):
        self.calls.append(kw)
        text = json.dumps(self.payload) if self.payload is not None else ""
        return SimpleNamespace(stop_reason=self.stop_reason, content=[SimpleNamespace(type="text", text=text)])


def _claude(monkeypatch, env, fake):
    import anthropic
    monkeypatch.setattr(env, "llm_provider", "anthropic")
    monkeypatch.setattr(anthropic, "Anthropic", lambda: fake)


def test_claude_storyboard_request_shape(env, monkeypatch):
    scene = {"title": "Playa", "description": "Ana salta", "setting": "Playa", "characters": ["Ana"],
             "camera": "Plano general", "duration_seconds": 5, "dialogue": [], "keyframe_prompt": "Ana jumping",
             "video_prompt": "Ana jumps"}
    fake = _FakeClaude({"refused": False, "refusal_reason": "", "title": "T", "logline": "L", "scenes": [scene]})
    _claude(monkeypatch, env, fake)
    out = llm.storyboard("Ana salta en la playa", CAST, style_label="Caricatura", aspect="9:16", max_scenes=4)
    assert out["provider"] == "anthropic" and out["scenes"][0]["title"] == "Playa"
    kw = fake.calls[0]
    assert kw["model"] == "claude-opus-5-5"
    assert kw["fallbacks"] == "default" and kw["betas"] == ["server-side-fallback-2026-07-01"]
    assert kw["output_config"]["format"]["type"] == "json_schema"
    assert "Ana" in kw["messages"][0]["content"] and "<instrucciones>" in kw["messages"][0]["content"]


def test_claude_refusals(env, monkeypatch):
    _claude(monkeypatch, env, _FakeClaude({"refused": True, "refusal_reason": "contenido sexual", "title": "",
                                            "logline": "", "scenes": []}))
    with pytest.raises(PolicyError, match="contenido sexual"):
        llm.storyboard("x", CAST, style_label="x", aspect="1:1", max_scenes=2)
    _claude(monkeypatch, env, _FakeClaude(None, stop_reason="refusal"))
    with pytest.raises(PolicyError):
        llm.storyboard("x", CAST, style_label="x", aspect="1:1", max_scenes=2)


def test_instructions_filter():
    with pytest.raises(PolicyError):
        policy.check_instructions("Haz un vídeo que parezca real del alcalde")
    policy.check_instructions("Ana y Luis celebran en la playa de Melilla, en estilo caricatura")


# ------------------------------------------------------------------ fal.ai (simulado)

def _fake_net(monkeypatch, statuses, result):
    calls = []

    def get_json(url, headers=None, method="GET", json_body=None, **kw):
        calls.append((method, url, json_body, headers))
        if method == "POST":
            return {"request_id": "r1", "status_url": "https://queue.fal.run/m/requests/r1/status",
                    "response_url": "https://queue.fal.run/m/requests/r1"}
        if url.endswith("/status"):
            return {"status": statuses.pop(0)}
        return result
    monkeypatch.setattr(net, "get_json", get_json)
    return calls


def test_fal_queue_flow(env, monkeypatch):
    monkeypatch.setattr(env, "fal_key", "clave")
    monkeypatch.setattr(env, "fal_poll_seconds", 0)
    calls = _fake_net(monkeypatch, ["IN_QUEUE", "IN_PROGRESS", "COMPLETED"], {"images": [{"url": "https://x/i.jpg"}]})
    cp: dict = {}
    out = fal.run("fal-ai/nano-banana/edit", {"prompt": "p"}, checkpoint=cp)
    assert fal.first_image_url(out) == "https://x/i.jpg"
    method, url, body, headers = calls[0]
    assert method == "POST" and url == "https://queue.fal.run/fal-ai/nano-banana/edit" and headers["Authorization"] == "Key clave"
    assert cp == {}  # el punto de control se limpia al terminar
    # Reanudación: con el trabajo ya enviado no se vuelve a pagar otro envío
    calls = _fake_net(monkeypatch, ["COMPLETED"], {"video": {"url": "https://x/v.mp4"}})
    cp = {"fal:m": {"request_id": "r1", "status_url": "https://queue.fal.run/m/requests/r1/status",
                    "response_url": "https://queue.fal.run/m/requests/r1"}}
    assert fal.video_url(fal.run("m", {}, checkpoint=cp)) == "https://x/v.mp4"
    assert not any(c[0] == "POST" for c in calls)


def test_fal_requires_key_and_handles_filters(env, monkeypatch):
    from app.errors import ProviderUnavailable
    with pytest.raises(ProviderUnavailable):
        fal.run("m", {})
    with pytest.raises(AppError, match="filtro"):
        fal.first_image_url({"images": []})
    monkeypatch.setattr(env, "fal_key", "k")
    _fake_net(monkeypatch, ["IN_QUEUE"], {})
    with pytest.raises(JobCancelled):
        fal.run("m", {}, should_cancel=lambda: True)


def test_fal_portrait_and_video_arguments(env, monkeypatch, tmp_path):
    monkeypatch.setattr(env, "fal_key", "k")
    seen = {}

    def fake_run(model, args, **kw):
        seen[model] = args
        return {"images": [{"url": "https://x/i.jpg"}]}
    monkeypatch.setattr(fal, "run", fake_run)
    monkeypatch.setattr(fal, "download", lambda url, dest, **kw: cv2.imwrite(str(dest), np.zeros((64, 64, 3), np.uint8)) and dest)
    info = images.portrait([_photo(tmp_path / "a.jpg")], "caricatura", "", tmp_path / "p.jpg")
    args = seen[env.fal_image_edit_model]
    assert info["provider"] == "fal" and args["image_urls"][0].startswith("data:image/jpeg;base64,")
    assert "caricature" in args["prompt"]
    a = video._fal_arguments("move", tmp_path / "p.jpg", 10)
    assert a["duration"] == "10" and a["image_url"].startswith("data:")
    assert video.estimate(7)["seconds"] == 5 and video.estimate(9)["seconds"] == 10
    assert video.estimate(9)["cost_usd"] == pytest.approx(0.5)


def test_fal_bad_image_rejected(env, monkeypatch, tmp_path):
    monkeypatch.setattr(env, "fal_key", "k")
    monkeypatch.setattr(fal, "run", lambda *a, **k: {"images": [{"url": "https://x"}]})
    monkeypatch.setattr(fal, "download", lambda url, dest, **kw: dest.write_bytes(b"no es una imagen") and dest)
    with pytest.raises(AppError, match="no es una imagen"):
        images.portrait([_photo(tmp_path / "a.jpg")], "comic", "", tmp_path / "p.jpg")


# ------------------------------------------------------------------ animática local (FFmpeg real)

@pytest.mark.parametrize("camera", ["Plano general con acercamiento", "paneo lateral", "alejamiento"])
def test_local_animatic(env, tmp_path, camera):
    kf = tmp_path / "kf.jpg"
    cv2.imwrite(str(kf), np.random.default_rng(2).integers(0, 255, (1280, 720, 3), dtype=np.uint8))
    info = video.animate({"duration_seconds": 4, "camera": camera}, kf, "comic", "9:16", tmp_path / "c.mp4", prov="local")
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=width,height:format=duration",
                          "-of", "default=nw=1", str(tmp_path / "c.mp4")], capture_output=True, text=True).stdout
    assert info["seconds"] == 4 and "width=720" in out and "height=1280" in out
    assert abs(float(next(l for l in out.splitlines() if l.startswith("duration=")).split("=")[1]) - 4) < 0.2
