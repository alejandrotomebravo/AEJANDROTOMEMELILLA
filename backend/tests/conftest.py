from __future__ import annotations

import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "data" / "test.db"))
    monkeypatch.setenv("SECRET_KEY", "test-secret")
    monkeypatch.setenv("INLINE_WORKER", "false")
    monkeypatch.setenv("ENV_FILE", str(tmp_path / "none.env"))
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://ejemplo.test")
    for k in ("FACESWAP_PROVIDER", "LIPSYNC_PROVIDER", "LIPSYNC_COMMAND", "ESPEAK_BINARY", "ALLOW_MINORS"):
        monkeypatch.delenv(k, raising=False)
    from app import config, db
    config.reload_settings()
    db.reset_connections()
    config.settings.ensure_dirs()
    yield config.settings
    db.reset_connections()


@pytest.fixture()
def client(env):
    from fastapi.testclient import TestClient
    from app.main import create_app
    with TestClient(create_app()) as c:
        yield c


@pytest.fixture()
def auth(client):
    r = client.post("/api/auth/register", json={"email": "ana@example.com", "password": "contraseña-segura"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def make_video(path: Path, seconds: float = 4.0, size: str = "640x360", audio: bool = True) -> Path:
    args = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
            "-i", f"testsrc=size={size}:rate=25:duration={seconds}"]
    if audio:
        args += ["-f", "lavfi", "-i", f"sine=frequency=300:sample_rate=48000:duration={seconds}"]
    args += ["-c:v", "libx264", "-pix_fmt", "yuv420p"]
    args += ["-c:a", "aac"] if audio else []
    args += [str(path)]
    subprocess.run(args, check=True)
    return path


@pytest.fixture()
def sample_video(tmp_path) -> Path:
    return make_video(tmp_path / "entrada.mov")


@pytest.fixture()
def fake_espeak(tmp_path, monkeypatch, env):
    """Sustituto del binario de eSpeak NG: genera un tono cuya duración depende del texto.
    Ejercita la ruta real de síntesis (subprocess + FFmpeg) sin depender de eSpeak."""
    script = tmp_path / "fake-espeak"
    script.write_text(
        "#!/usr/bin/env python3\n"
        "import sys, subprocess\n"
        "a = sys.argv[1:]\n"
        "out = a[a.index('-w') + 1]\n"
        "text = a[a.index('--') + 1]\n"
        "dur = max(0.3, len(text) * 0.06)\n"
        "subprocess.run(['ffmpeg','-loglevel','error','-y','-f','lavfi','-i',f'sine=frequency=220:duration={dur}',out],check=True)\n"
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setattr(env, "espeak_binary", str(script))
    return script
