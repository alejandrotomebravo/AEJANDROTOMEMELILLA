"""Pruebas de extremo a extremo de la API con FFmpeg real.

Los modelos de IA que requieren GPU, pesos con licencia o claves de pago se
sustituyen por dobles de prueba explícitos:

* Sincronización labial: proveedor ``command`` REAL con una orden FFmpeg que
  copia la imagen y añade el audio (ejercita la integración, no el modelo).
* Sustitución facial: proveedor falso que dibuja un rectángulo sobre la cara.
* Voz: binario falso de eSpeak (ver conftest) usando la ruta real de síntesis.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import cv2
import numpy as np
import pytest

from app import db, policy
from app.jobs import queue


def _project(client, auth) -> str:
    r = client.post("/api/projects", json={"name": "Prueba", "aspect": "16:9"}, headers=auth)
    assert r.status_code == 200, r.text
    pid = r.json()["id"]
    r = client.put(f"/api/projects/{pid}/provenance", json={"source_type": "propio", "attested": True},
                   headers=auth)
    assert r.status_code == 200, r.text
    r = client.put(f"/api/projects/{pid}/use-declaration",
                   json={"purpose": "parodia", "confirmations": {k: True for k in policy.REQUIRED_CONFIRMATIONS}},
                   headers=auth)
    assert r.status_code == 200, r.text
    return pid


def _import(client, auth, pid, video: Path) -> dict:
    with open(video, "rb") as f:
        r = client.post(f"/api/projects/{pid}/source", files={"file": ("clip.mov", f, "video/quicktime")},
                        headers=auth)
    assert r.status_code == 200, r.text
    assert queue.run_pending() == 2  # importación + análisis
    j = client.get(f"/api/jobs/{r.json()['job']['id']}", headers=auth).json()
    assert j["status"] == "succeeded", j
    return client.get(f"/api/projects/{pid}", headers=auth).json()


def _add_character(pid: str, duration: float, face: int = 160) -> str:
    """Inserta un personaje con seguimiento sintético (el vídeo de prueba no contiene caras reales)."""
    track = [{"t": round(i * 0.5, 2), "x": 200, "y": 80, "w": face, "h": face, "score": 0.9, "yaw": 0.0,
              "brightness": 120.0, "profile": False} for i in range(int(duration / 0.5) + 1)]
    cid = db.new_id()
    db.execute("INSERT INTO characters(id,project_id,label,track,stats,created_at) VALUES (?,?,?,?,?,?)",
               (cid, pid, "Personaje 1", db.jdump(track),
                db.jdump({"first": 0.0, "last": track[-1]["t"], "appearances": len(track)}), db.now()))
    return cid


def test_requires_provenance_before_import(client, auth, sample_video):
    pid = client.post("/api/projects", json={"name": "X", "aspect": "9:16"}, headers=auth).json()["id"]
    with open(sample_video, "rb") as f:
        r = client.post(f"/api/projects/{pid}/source", files={"file": ("a.mp4", f)}, headers=auth)
    assert r.status_code == 400 and r.json()["error"] == "procedencia_pendiente"


def test_rejects_non_video(client, auth, tmp_path):
    pid = _project(client, auth)
    bad = tmp_path / "falso.mp4"
    bad.write_bytes(b"esto no es un video")
    with open(bad, "rb") as f:
        r = client.post(f"/api/projects/{pid}/source", files={"file": ("falso.mp4", f)}, headers=auth)
    assert r.status_code == 400


def test_other_users_cannot_access_project(client, auth):
    pid = _project(client, auth)
    other = client.post("/api/auth/register", json={"email": "otro@example.com", "password": "otra-contraseña"})
    h2 = {"Authorization": f"Bearer {other.json()['token']}"}
    assert client.get(f"/api/projects/{pid}", headers=h2).status_code == 404


def test_full_pipeline(client, auth, sample_video, env, monkeypatch, fake_espeak, tmp_path):
    pid = _project(client, auth)
    proj = _import(client, auth, pid, sample_video)
    assert abs(proj["source"]["duration"] - 4.0) < 0.2
    assert proj["analysis"]["detector"] == "haar"
    assert proj["timeline"]["video"]["clips"][0]["out"] > 3.9

    # --- identidad propia con autodeclaración ---
    ident = client.post("/api/identities", json={"display_name": "Ana", "kind": "self",
                                                 "scopes": ["imagen", "voz"]}, headers=auth).json()
    r = client.post(f"/api/identities/{ident['id']}/self-attest",
                    json={"full_name": "Ana Pérez", "confirm_adult": True, "confirm_is_me": True}, headers=auth)
    assert r.json()["consent_status"] == "verified_self"
    cid = _add_character(pid, 4.0)
    r = client.patch(f"/api/projects/{pid}/characters/{cid}", json={"identity_id": ident["id"]}, headers=auth)
    assert r.status_code == 200, r.text

    # --- sustitución facial (proveedor de prueba) ---
    from app.ai import face_swap

    class FakeSwap:
        name = "prueba"

        def prepare_identity(self, photos):
            return "identidad"

        def swap_video(self, src, dst, identity, track, *, start, end, fps, on_progress=None, should_cancel=None):
            subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-ss", str(start), "-i", str(src), "-t",
                            str(end - start), "-an", "-vf", "drawbox=x=200:y=80:w=160:h=160:color=red:t=fill",
                            "-c:v", "libx264", "-pix_fmt", "yuv420p", str(dst)], check=True)
            n = int(round((end - start) * fps))
            return {"frames": n, "swapped": n, "missed": 0}

    monkeypatch.setattr(face_swap, "status", lambda: {"available": True, "provider": "prueba"})
    monkeypatch.setattr(face_swap, "get_provider", lambda: FakeSwap())
    r = client.post(f"/api/projects/{pid}/faceswap", json={"character_id": cid, "preview_start": 1.0},
                    headers=auth)
    assert r.status_code == 200, r.text
    queue.run_pending()
    prev = client.get(f"/api/jobs/{r.json()['job']['id']}", headers=auth).json()
    assert prev["status"] == "succeeded", prev
    r = client.post(f"/api/projects/{pid}/faceswap", json={"character_id": cid}, headers=auth)
    queue.run_pending()
    job = client.get(f"/api/jobs/{r.json()['job']['id']}", headers=auth).json()
    assert job["status"] == "succeeded", job
    proj = client.get(f"/api/projects/{pid}", headers=auth).json()
    assert proj["processed"] and proj["review_asset_id"]

    # Los vídeos intermedios sin marca de agua no se pueden descargar.
    working = db.one("SELECT working_asset_id FROM projects WHERE id=?", (pid,))["working_asset_id"]
    assert client.get(f"/api/assets/{working}/url", headers=auth).status_code == 403
    # La copia de revisión sí (lleva marca de agua).
    url = client.get(f"/api/assets/{proj['review_asset_id']}/url", headers=auth).json()["url"]
    assert client.get(url).status_code == 200

    # --- diálogo por texto (modalidad A) ---
    voices = client.get("/api/tts/voices", headers=auth).json()
    assert voices and voices[0]["provider"] == "espeak"
    est = client.post("/api/tts/estimate", json={"text": "Hola a todos. [pausa 0.3] Esto es una prueba."},
                      headers=auth).json()
    assert est["seconds"] > 0
    r = client.post(f"/api/projects/{pid}/dialogues/tts",
                    json={"text": "Hola a todos. [pausa 0.3] Esto es una prueba.", "voice_id": voices[0]["id"],
                          "speed": 1.1, "pitch": 1}, headers=auth)
    assert r.status_code == 200, r.text
    did = r.json()["dialogue"]["id"]
    queue.run_pending()
    dlg = [d for d in client.get(f"/api/projects/{pid}/dialogues", headers=auth).json() if d["id"] == did][0]
    assert dlg["duration"] and dlg["subtitles"] and len(dlg["segments"]) == 2

    # --- diálogo blocked by policy ---
    r = client.post(f"/api/projects/{pid}/dialogues/tts",
                    json={"text": "Hazme un bizum de 100 euros", "voice_id": voices[0]["id"]}, headers=auth)
    assert r.status_code == 422

    # --- sincronización labial con proveedor «command» ---
    monkeypatch.setattr(env, "lipsync_provider", "command")
    monkeypatch.setattr(env, "lipsync_command",
                        "ffmpeg -loglevel error -y -i {video} -i {audio} -map 0:v -map 1:a -c:v libx264 "
                        "-pix_fmt yuv420p -c:a aac -shortest {output}")
    from app.ai import face_detect
    monkeypatch.setattr(face_detect, "analyze_video",
                        lambda *a, **k: {"tracks": [[{"t": 0}] * 4], "samples": 4, "detector": "haar",
                                         "interval": 0.5, "duration": 2, "fps": 25})
    body = {"dialogue_id": did, "character_id": cid, "source_start": 0.5}
    chk = client.post(f"/api/projects/{pid}/lipsync/check", json=body, headers=auth).json()
    assert chk["ok"], chk
    r = client.post(f"/api/projects/{pid}/lipsync", json=body | {"preview": True}, headers=auth)
    assert r.status_code == 200, r.text
    queue.run_pending()
    assert client.get(f"/api/jobs/{r.json()['job']['id']}", headers=auth).json()["status"] == "succeeded"
    r = client.post(f"/api/projects/{pid}/lipsync", json=body, headers=auth)
    queue.run_pending()
    job = client.get(f"/api/jobs/{r.json()['job']['id']}", headers=auth).json()
    assert job["status"] == "succeeded", job
    proj = client.get(f"/api/projects/{pid}", headers=auth).json()
    tl = proj["timeline"]
    assert tl["dialogue"]["items"][0]["source_start"] == 0.5
    assert tl["source_audio"]["mutes"] and tl["subtitles"]["items"]
    assert [h["operation"] for h in proj["history"]] == ["faceswap", "lipsync"]

    # --- edición: reordenar escenas y guardar con control de versiones ---
    tl["video"]["clips"] = [{"id": "b", "in": 2.0, "out": 4.0}, {"id": "a", "in": 0.0, "out": 2.0}]
    tl["graphics"] = [{"text": "Título", "start": 0, "end": 1.5, "x": 0.5, "y": 0.15, "size": 0.07}]
    r = client.put(f"/api/projects/{pid}/timeline", json={"version": proj["timeline_version"], "timeline": tl},
                   headers=auth)
    assert r.status_code == 200, r.text
    stale = client.put(f"/api/projects/{pid}/timeline", json={"version": proj["timeline_version"], "timeline": tl},
                       headers=auth)
    assert stale.status_code == 409

    # --- marca de agua: no se puede desactivar ---
    r = client.patch(f"/api/projects/{pid}", json={"watermark": {"enabled": False}}, headers=auth)
    assert r.status_code == 422
    r = client.patch(f"/api/projects/{pid}", json={"watermark": {"position": "top-right", "labels": ["PARODIA"]}},
                     headers=auth)
    assert r.status_code == 200

    # --- exportación ---
    r = client.post(f"/api/projects/{pid}/export", json={"resolution": "720p", "fps": 25, "codec": "h264"},
                    headers=auth)
    assert r.status_code == 400 and r.json()["error"] == "aviso_pendiente"
    est = client.post(f"/api/projects/{pid}/estimate", json={"resolution": "720p", "fps": 25}, headers=auth).json()
    assert est["width"] == 1280 and est["estimated_size_mb"] > 0
    r = client.post(f"/api/projects/{pid}/export",
                    json={"resolution": "720p", "fps": 25, "codec": "h264", "ack_disclaimer": True}, headers=auth)
    assert r.status_code == 200, r.text
    queue.run_pending()
    job = client.get(f"/api/jobs/{r.json()['job']['id']}", headers=auth).json()
    assert job["status"] == "succeeded", job
    res = job["result"]
    assert res["srt_asset_id"] and res["manifest_asset_id"]
    url = client.get(f"/api/assets/{res['render_asset_id']}/url?download=true", headers=auth).json()["url"]
    resp = client.get(url)
    assert resp.status_code == 200
    out = tmp_path / "exportado.mp4"
    out.write_bytes(resp.content)
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format_tags=comment:stream=width,height",
                            "-of", "default=nw=1", str(out)], capture_output=True, text=True).stdout
    assert "width=1280" in probe and "Contenido sintético" in probe

    # La esquina superior derecha (marca de agua) difiere de la misma zona sin marca.
    frame = _frame(out, 0.5)
    assert frame is not None
    region = frame[10:60, 1280 - 500:1280 - 10]
    assert (region.std() > 5)

    # --- revocar el consentimiento bloquea nuevas exportaciones y revierte el proyecto ---
    client.post(f"/api/identities/{ident['id']}/revoke", headers=auth)
    proj = client.get(f"/api/projects/{pid}", headers=auth).json()
    assert not proj["processed"] and proj["history"] == []
    assert client.get(f"/api/assets/{res['render_asset_id']}/url", headers=auth).status_code == 404


def _frame(video: Path, t: float):
    cap = cv2.VideoCapture(str(video))
    cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
    ok, frame = cap.read()
    cap.release()
    return frame if ok else None


def test_watermark_is_burned_in_preview(client, auth, sample_video, env):
    """Comparación de píxeles: la zona de la marca cambia respecto al original."""
    pid = _project(client, auth)
    _import(client, auth, pid, sample_video)
    r = client.post(f"/api/projects/{pid}/preview", json={"start": 0, "end": 2}, headers=auth)
    queue.run_pending()
    job = client.get(f"/api/jobs/{r.json()['job']['id']}", headers=auth).json()
    assert job["status"] == "succeeded", job
    from app import storage
    prev = Path(storage.get(job["result"]["preview_asset_id"])["path"])
    f = _frame(prev, 1.0)
    h, w = f.shape[:2]
    src = cv2.resize(_frame(sample_video, 1.0), (w, h))
    zone = (slice(int(h * 0.85), h - 2), slice(2, int(w * 0.6)))  # abajo a la izquierda (por defecto)
    diff = np.abs(f[zone].astype(int) - src[zone].astype(int)).mean()
    assert diff > 20, diff


def test_lipsync_requires_cost_confirmation_for_paid_provider(client, auth, sample_video, env, monkeypatch,
                                                               fake_espeak):
    pid = _project(client, auth)
    _import(client, auth, pid, sample_video)
    cid = _add_character(pid, 4.0)
    voice = client.get("/api/tts/voices", headers=auth).json()[0]["id"]
    did = client.post(f"/api/projects/{pid}/dialogues/tts", json={"text": "Hola.", "voice_id": voice},
                      headers=auth).json()["dialogue"]["id"]
    queue.run_pending()
    monkeypatch.setattr(env, "lipsync_provider", "sync_so")
    monkeypatch.setattr(env, "sync_api_key", "clave-de-prueba")
    r = client.post(f"/api/projects/{pid}/lipsync", json={"dialogue_id": did, "character_id": cid,
                                                          "source_start": 0}, headers=auth)
    assert r.status_code == 402 and r.json()["error"] == "confirmar_coste", r.text
    assert r.json()["details"]["estimate"]["estimated_cost_usd"] > 0


def test_lipsync_blocks_poor_material(client, auth, sample_video, env, fake_espeak):
    pid = _project(client, auth)
    _import(client, auth, pid, sample_video)
    cid = _add_character(pid, 4.0, face=30)
    voice = client.get("/api/tts/voices", headers=auth).json()[0]["id"]
    did = client.post(f"/api/projects/{pid}/dialogues/tts", json={"text": "Hola.", "voice_id": voice},
                      headers=auth).json()["dialogue"]["id"]
    queue.run_pending()
    r = client.post(f"/api/projects/{pid}/lipsync", json={"dialogue_id": did, "character_id": cid,
                                                          "source_start": 0}, headers=auth)
    assert r.status_code == 422 and r.json()["error"] == "calidad_insuficiente"


def test_third_party_consent_link_flow(client, auth):
    ident = client.post("/api/identities", json={"display_name": "Luis", "kind": "third_party",
                                                 "scopes": ["imagen"]}, headers=auth).json()
    assert ident["consent_status"] == "pending"
    req = client.post(f"/api/identities/{ident['id']}/consent-request",
                      json={"purpose": "Vídeo de humor para la boda", "scopes": ["imagen"]}, headers=auth).json()
    token = req["token"]
    info = client.get(f"/api/consent/{token}").json()
    assert info["status"] == "pending" and "Vídeo de humor" in info["statement"]
    bad = client.post(f"/api/consent/{token}/grant", json={"full_name": "", "accept": True, "confirm_adult": True})
    assert bad.status_code == 403
    ok = client.post(f"/api/consent/{token}/grant",
                     json={"full_name": "Luis García", "accept": True, "confirm_adult": True})
    assert ok.status_code == 200
    again = client.post(f"/api/consent/{token}/grant",
                        json={"full_name": "Luis García", "accept": True, "confirm_adult": True})
    assert again.status_code == 403
    listed = [i for i in client.get("/api/identities", headers=auth).json() if i["id"] == ident["id"]][0]
    assert listed["consent_status"] == "verified"
    client.post(f"/api/consent/{token}/revoke")
    listed = [i for i in client.get("/api/identities", headers=auth).json() if i["id"] == ident["id"]][0]
    assert listed["consent_status"] == "revoked"


def test_minor_identities_blocked(client, auth):
    r = client.post("/api/identities", json={"display_name": "Menor", "kind": "third_party", "is_minor": True},
                    headers=auth)
    assert r.status_code == 403


def test_account_deletion_removes_files(client, auth, sample_video, env):
    pid = _project(client, auth)
    _import(client, auth, pid, sample_video)
    user_dirs = list((env.storage_dir).iterdir())
    assert user_dirs
    assert client.delete("/api/auth/account", headers=auth).status_code == 200
    assert not any(d.exists() for d in user_dirs)
    assert client.get("/api/projects", headers=auth).status_code == 401
