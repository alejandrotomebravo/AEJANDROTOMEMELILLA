"""Pruebas de extremo a extremo de la API con FFmpeg real.

Los modelos de IA que requieren GPU, pesos con licencia o claves de pago se
sustituyen por dobles de prueba explícitos:

* Sincronización labial: proveedor ``command`` REAL con una orden FFmpeg que
  copia la imagen y añade el audio (ejercita la integración, no el modelo).
* Sustitución facial: proveedor falso que dibuja un rectángulo sobre la cara.
* Voz: binario falso de eSpeak (ver conftest) usando la ruta real de síntesis.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import cv2
import numpy as np

from app import db, policy, storage
from app.jobs import queue

from .conftest import make_video

PROV = {"source_type": "propio", "attested": True, "can_modify": True, "can_distribute": True}


def _project(client, auth, aspect="16:9") -> str:
    r = client.post("/api/projects", json={"name": "Prueba", "aspect": aspect}, headers=auth)
    assert r.status_code == 200, r.text
    pid = r.json()["id"]
    r = client.put(f"/api/projects/{pid}/use-declaration",
                   json={"purpose": "parodia", "distribution": "redes",
                         "confirmations": {k: True for k in policy.REQUIRED_CONFIRMATIONS}}, headers=auth)
    assert r.status_code == 200, r.text
    return pid


def _upload(client, auth, pid, *videos: Path, prov=PROV, expect=200, **form) -> list[dict]:
    files = [("files", (v.name, open(v, "rb"), "video/mp4")) for v in videos]
    r = client.post(f"/api/projects/{pid}/sources/upload", files=files,
                    data={"provenance": json.dumps(prov), **{k: str(v) for k, v in form.items()}}, headers=auth)
    for _, (_, fh, _) in files:
        fh.close()
    assert r.status_code == expect, r.text
    if expect != 200:
        return r.json()
    queue.run_pending()
    for c in r.json()["created"]:
        j = client.get(f"/api/jobs/{c['job']['id']}", headers=auth).json()
        assert j["status"] == "succeeded", j
    return client.get(f"/api/projects/{pid}/sources", headers=auth).json()


def _add_character(pid: str, sid: str, duration: float, face: int = 160) -> str:
    """Personaje con seguimiento sintético (los vídeos de prueba no contienen caras reales)."""
    track = [{"t": round(i * 0.5, 2), "x": 200, "y": 80, "w": face, "h": face, "score": 0.9, "yaw": 0.0,
              "brightness": 120.0, "profile": False} for i in range(int(duration / 0.5) + 1)]
    cid = db.new_id()
    db.execute("INSERT INTO characters(id,project_id,source_id,label,track,stats,created_at) VALUES (?,?,?,?,?,?,?)",
               (cid, pid, sid, "Personaje 1", db.jdump(track),
                db.jdump({"first": 0.0, "last": track[-1]["t"], "appearances": len(track)}), db.now()))
    return cid


def _frame(video: Path, t: float):
    cap = cv2.VideoCapture(str(video))
    cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
    ok, frame = cap.read()
    cap.release()
    return frame if ok else None


class FakeSwap:
    name = "prueba"

    def prepare_identity(self, photos):
        return "identidad"

    def swap_video(self, src, dst, identity, track, *, start, end, fps, intensity=1.0, on_progress=None,
                   should_cancel=None):
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-ss", str(start), "-i", str(src), "-t",
                        str(end - start), "-an", "-vf", f"drawbox=x=200:y=80:w=160:h=160:color=red@{intensity}:t=fill",
                        "-c:v", "libx264", "-pix_fmt", "yuv420p", str(dst)], check=True)
        n = int(round((end - start) * fps))
        return {"frames": n, "swapped": n, "missed": 0}


def test_upload_requires_declarations(client, auth, sample_video):
    pid = client.post("/api/projects", json={"name": "X", "aspect": "9:16"}, headers=auth).json()["id"]
    r = _upload(client, auth, pid, sample_video, expect=400)
    assert r["error"] == "declaracion_pendiente"
    pid = _project(client, auth)
    r = _upload(client, auth, pid, sample_video, prov={"source_type": "propio", "attested": True}, expect=422)
    assert "MODIFICAR" in r["message"]


def test_rejects_non_video_and_disguised_files(client, auth, tmp_path):
    pid = _project(client, auth)
    bad = tmp_path / "falso.mp4"
    bad.write_bytes(b"esto no es un video")
    _upload(client, auth, pid, bad, expect=400)
    playlist = tmp_path / "lista.mp4"  # lista HLS disfrazada: FFmpeg podría leer archivos locales o URLs
    playlist.write_text("#EXTM3U\n#EXTINF:1,\nfile:///etc/passwd\n#EXT-X-ENDLIST\n")
    _upload(client, auth, pid, playlist, expect=400)


def test_other_users_cannot_access_project(client, auth):
    pid = _project(client, auth)
    other = client.post("/api/auth/register", json={"email": "otro@example.com", "password": "otra-contraseña"})
    h2 = {"Authorization": f"Bearer {other.json()['token']}"}
    assert client.get(f"/api/projects/{pid}", headers=h2).status_code == 404
    assert client.get(f"/api/projects/{pid}/sources", headers=h2).status_code == 404


def test_fragment_import(client, auth, tmp_path):
    pid = _project(client, auth)
    src = _upload(client, auth, pid, make_video(tmp_path / "largo.mp4", seconds=6), start=1.5, end=4.0)
    assert abs(src[0]["duration"] - 2.5) < 0.15
    assert src[0]["analysis"]["sprite"]["count"] >= 1 and src[0]["sprite_asset_id"]


def test_full_multi_source_pipeline(client, auth, env, monkeypatch, fake_espeak, tmp_path):
    pid = _project(client, auth)
    v1 = make_video(tmp_path / "escena1.mov", seconds=4.0)
    v2 = make_video(tmp_path / "vertical.mp4", seconds=3.0, size="360x640", audio=False)
    sources = _upload(client, auth, pid, v1, v2)  # carga por lotes
    assert len(sources) == 2 and all(s["status"] == "ready" for s in sources)
    s1, s2 = sources
    proj = client.get(f"/api/projects/{pid}", headers=auth).json()
    assert [c["source_id"] for c in proj["timeline"]["video"]["clips"]] == [s1["id"], s2["id"]]

    # --- identidad propia y sustitución facial con intensidad ---
    ident = client.post("/api/identities", json={"display_name": "Ana", "kind": "self",
                                                 "scopes": ["imagen", "voz"]}, headers=auth).json()
    client.post(f"/api/identities/{ident['id']}/self-attest",
                json={"full_name": "Ana Pérez", "confirm_adult": True, "confirm_is_me": True}, headers=auth)
    cid = _add_character(pid, s1["id"], 4.0)
    assert client.patch(f"/api/projects/{pid}/characters/{cid}", json={"identity_id": ident["id"]},
                        headers=auth).status_code == 200
    from app.ai import face_swap
    monkeypatch.setattr(face_swap, "status", lambda: {"available": True, "provider": "prueba"})
    monkeypatch.setattr(face_swap, "get_provider", lambda: FakeSwap())
    r = client.post(f"/api/projects/{pid}/faceswap", json={"character_id": cid, "intensity": 0.2}, headers=auth)
    assert r.status_code == 400
    r = client.post(f"/api/projects/{pid}/faceswap", json={"character_id": cid, "intensity": 0.8}, headers=auth)
    queue.run_pending()
    job = client.get(f"/api/jobs/{r.json()['job']['id']}", headers=auth).json()
    assert job["status"] == "succeeded", job
    s1 = client.get(f"/api/projects/{pid}/sources", headers=auth).json()[0]
    assert s1["processed"] and s1["review_asset_id"]
    proj = client.get(f"/api/projects/{pid}", headers=auth).json()
    assert proj["faces_altered"] and "ROSTROS ALTERADOS CON IA" in proj["watermark"]["labels"]

    # Los intermedios sin marca no se sirven; la revisión (con marca) sí.
    working = db.one("SELECT working_asset_id FROM sources WHERE id=?", (s1["id"],))["working_asset_id"]
    assert client.get(f"/api/assets/{working}/url", headers=auth).status_code == 403
    url = client.get(f"/api/assets/{s1['review_asset_id']}/url", headers=auth).json()["url"]
    assert client.get(url).status_code == 200

    # --- diálogo y ajuste a la escena ---
    voice = client.get("/api/tts/voices", headers=auth).json()[0]["id"]
    long_text = "Esta es una frase muy larga que no cabe en la escena. " * 3
    fit = client.post(f"/api/projects/{pid}/dialogues/fit", json={"text": long_text, "source_id": s1["id"],
                                                                  "character_id": cid, "start": 0.5},
                      headers=auth).json()
    assert not fit["fits"] and {s["type"] for s in fit["suggestions"]} >= {"recortar", "dividir"}
    r = client.post(f"/api/projects/{pid}/dialogues/tts", json={"text": "Hola. [pausa 0.3] Prueba.", "voice_id": voice,
                                                                 "speed": 1.1}, headers=auth)
    did = r.json()["dialogue"]["id"]
    queue.run_pending()

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
    r = client.post(f"/api/projects/{pid}/lipsync", json=body, headers=auth)
    assert r.status_code == 200, r.text
    queue.run_pending()
    job = client.get(f"/api/jobs/{r.json()['job']['id']}", headers=auth).json()
    assert job["status"] == "succeeded", job
    proj = client.get(f"/api/projects/{pid}", headers=auth).json()
    tl = proj["timeline"]
    assert tl["dialogue"]["items"][0]["source_id"] == s1["id"]
    assert tl["source_audio"]["mutes"][0]["source_id"] == s1["id"]

    # --- edición: reordenar, velocidad, color, fundidos, gráficos ---
    c1, c2 = tl["video"]["clips"]
    c2.update({"speed": 1.5, "fade_in": 0.3, "color": {"brightness": 0.05, "contrast": 1.1, "saturation": 1.3}})
    c1.update({"volume": 0.0, "fade_out": 0.5})
    tl["video"]["clips"] = [c2, c1]
    tl["graphics"] = [{"text": "Título", "start": 0, "end": 1.5, "x": 0.5, "y": 0.15, "size": 0.07}]
    r = client.put(f"/api/projects/{pid}/timeline", json={"version": proj["timeline_version"], "timeline": tl},
                   headers=auth)
    assert r.status_code == 200, r.text
    assert abs(r.json()["duration"] - (3.0 / 1.5 + 4.0)) < 0.05
    assert client.put(f"/api/projects/{pid}/timeline", json={"version": proj["timeline_version"], "timeline": tl},
                      headers=auth).status_code == 409

    # --- marca de agua: no se puede desactivar ---
    assert client.patch(f"/api/projects/{pid}", json={"watermark": {"enabled": False}}, headers=auth).status_code == 422

    # --- exportación ---
    r = client.post(f"/api/projects/{pid}/export", json={"resolution": "720p", "fps": 25}, headers=auth)
    assert r.json()["error"] == "aviso_pendiente"
    r = client.post(f"/api/projects/{pid}/export", json={"resolution": "720p", "fps": 25, "ack_disclaimer": True},
                    headers=auth)
    queue.run_pending()
    job = client.get(f"/api/jobs/{r.json()['job']['id']}", headers=auth).json()
    assert job["status"] == "succeeded", job
    res = job["result"]
    out = Path(storage.get(res["render_asset_id"])["path"])
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration:format_tags=comment",
                            "-of", "default=nw=1", str(out)], capture_output=True, text=True).stdout
    assert "Contenido sintético" in probe
    dur = float(next(l.split("=")[1] for l in probe.splitlines() if l.startswith("duration=")))
    assert abs(dur - 6.0) < 0.2, dur
    assert "ROSTROS ALTERADOS CON IA" in storage.get(res["render_asset_id"])["meta"]["watermark"]["labels"]

    # --- revocar el consentimiento revierte la fuente y elimina exportaciones ---
    client.post(f"/api/identities/{ident['id']}/revoke", headers=auth)
    s1 = client.get(f"/api/projects/{pid}/sources", headers=auth).json()[0]
    assert not s1["processed"] and s1["history"] == []
    assert client.get(f"/api/assets/{res['render_asset_id']}/url", headers=auth).status_code == 404


def test_watermark_is_burned_in_preview(client, auth, sample_video):
    pid = _project(client, auth)
    src = _upload(client, auth, pid, sample_video)[0]
    r = client.post(f"/api/projects/{pid}/preview", json={"start": 0, "end": 2}, headers=auth)
    queue.run_pending()
    job = client.get(f"/api/jobs/{r.json()['job']['id']}", headers=auth).json()
    assert job["status"] == "succeeded", job
    prev = Path(storage.get(job["result"]["preview_asset_id"])["path"])
    f = _frame(prev, 1.0)
    h, w = f.shape[:2]
    src_frame = cv2.resize(_frame(sample_video, 1.0), (w, h))
    zone = (slice(int(h * 0.85), h - 2), slice(2, int(w * 0.6)))
    diff = np.abs(f[zone].astype(int) - src_frame[zone].astype(int)).mean()
    assert diff > 20, diff
    assert src["status"] == "ready"


def _dialogue_ready(client, auth, pid):
    voice = client.get("/api/tts/voices", headers=auth).json()[0]["id"]
    did = client.post(f"/api/projects/{pid}/dialogues/tts", json={"text": "Hola.", "voice_id": voice},
                      headers=auth).json()["dialogue"]["id"]
    queue.run_pending()
    return did


def test_lipsync_requires_cost_confirmation_for_paid_provider(client, auth, sample_video, env, monkeypatch,
                                                               fake_espeak):
    pid = _project(client, auth)
    sid = _upload(client, auth, pid, sample_video)[0]["id"]
    cid = _add_character(pid, sid, 4.0)
    did = _dialogue_ready(client, auth, pid)
    monkeypatch.setattr(env, "lipsync_provider", "sync_so")
    monkeypatch.setattr(env, "sync_api_key", "clave-de-prueba")
    r = client.post(f"/api/projects/{pid}/lipsync", json={"dialogue_id": did, "character_id": cid,
                                                          "source_start": 0}, headers=auth)
    assert r.status_code == 402 and r.json()["error"] == "confirmar_coste", r.text
    assert r.json()["details"]["estimate"]["estimated_cost_usd"] > 0


def test_lipsync_blocks_poor_material(client, auth, sample_video, env, fake_espeak):
    pid = _project(client, auth)
    sid = _upload(client, auth, pid, sample_video)[0]["id"]
    cid = _add_character(pid, sid, 4.0, face=30)
    did = _dialogue_ready(client, auth, pid)
    r = client.post(f"/api/projects/{pid}/lipsync", json={"dialogue_id": did, "character_id": cid,
                                                          "source_start": 0}, headers=auth)
    assert r.status_code == 422 and r.json()["error"] == "calidad_insuficiente"


def test_delete_source_cleans_timeline(client, auth, tmp_path):
    pid = _project(client, auth)
    s = _upload(client, auth, pid, make_video(tmp_path / "a.mp4", 2), make_video(tmp_path / "b.mp4", 2))
    assert client.delete(f"/api/projects/{pid}/sources/{s[1]['id']}", headers=auth).status_code == 200
    tl = client.get(f"/api/projects/{pid}", headers=auth).json()["timeline"]
    assert [c["source_id"] for c in tl["video"]["clips"]] == [s[0]["id"]]


def test_third_party_consent_link_flow(client, auth):
    ident = client.post("/api/identities", json={"display_name": "Luis", "kind": "third_party",
                                                 "scopes": ["imagen"]}, headers=auth).json()
    req = client.post(f"/api/identities/{ident['id']}/consent-request",
                      json={"purpose": "Vídeo de humor para la boda", "scopes": ["imagen"]}, headers=auth).json()
    token = req["token"]
    info = client.get(f"/api/consent/{token}").json()
    assert info["status"] == "pending" and "Vídeo de humor" in info["statement"]
    assert client.post(f"/api/consent/{token}/grant", json={"full_name": "", "accept": True,
                                                            "confirm_adult": True}).status_code == 403
    assert client.post(f"/api/consent/{token}/grant", json={"full_name": "Luis García", "accept": True,
                                                            "confirm_adult": True}).status_code == 200
    assert client.post(f"/api/consent/{token}/grant", json={"full_name": "Luis García", "accept": True,
                                                            "confirm_adult": True}).status_code == 403
    listed = [i for i in client.get("/api/identities", headers=auth).json() if i["id"] == ident["id"]][0]
    assert listed["consent_status"] == "verified"
    client.post(f"/api/consent/{token}/revoke")
    listed = [i for i in client.get("/api/identities", headers=auth).json() if i["id"] == ident["id"]][0]
    assert listed["consent_status"] == "revoked"


def test_minor_identities_blocked(client, auth):
    r = client.post("/api/identities", json={"display_name": "Menor", "kind": "third_party", "is_minor": True},
                    headers=auth)
    assert r.status_code == 403


def test_project_management_and_account_deletion(client, auth, sample_video, env):
    pid = _project(client, auth)
    _upload(client, auth, pid, sample_video)
    summary = client.get("/api/projects", headers=auth).json()[0]
    assert summary["sources"] == 1 and summary["storage_bytes"] > 0 and summary["expires_at"]
    dup = client.post(f"/api/projects/{pid}/duplicate", headers=auth).json()
    assert dup["use_declaration"] and dup["sources"] == []
    user_dirs = list(env.storage_dir.iterdir())
    assert client.delete("/api/auth/account", headers=auth).status_code == 200
    assert not any(d.exists() for d in user_dirs)
    assert client.get("/api/projects", headers=auth).status_code == 401
