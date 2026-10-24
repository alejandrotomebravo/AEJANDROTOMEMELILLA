"""Pruebas de extremo a extremo de la API del estudio con FFmpeg real y proveedores locales.

Los servicios de pago (Claude, fal.ai) no se llaman: el entorno de pruebas no tiene claves.
Se usan los proveedores locales reales (guion básico, filtros OpenCV, animática FFmpeg) y un
binario falso de eSpeak (ver conftest) que ejercita la ruta real de síntesis.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import cv2
import numpy as np

from app import db, policy, storage
from app.jobs import queue


def _project(client, auth, aspect="9:16", style="caricatura") -> str:
    r = client.post("/api/projects", json={"name": "Prueba", "aspect": aspect, "style": style}, headers=auth)
    assert r.status_code == 200, r.text
    pid = r.json()["id"]
    r = client.put(f"/api/projects/{pid}/use-declaration",
                   json={"purpose": "parodia", "distribution": "redes",
                         "confirmations": {k: True for k in policy.REQUIRED_CONFIRMATIONS}}, headers=auth)
    assert r.status_code == 200, r.text
    return pid


def _self_identity(client, auth, env, tmp_path) -> str:
    """Identidad propia con una foto sintética registrada directamente (la subida real exige
    detectar una cara, y las imágenes de prueba no contienen caras reales)."""
    ident = client.post("/api/identities", json={"display_name": "Ana", "kind": "self",
                                                 "scopes": ["imagen", "voz"]}, headers=auth).json()
    r = client.post(f"/api/identities/{ident['id']}/self-attest",
                    json={"full_name": "Ana Pérez", "confirm_adult": True, "confirm_is_me": True}, headers=auth)
    assert r.status_code == 200, r.text
    uid = db.one("SELECT user_id FROM identities WHERE id=?", (ident["id"],))["user_id"]
    aid, dest = storage.new_path(uid, ".jpg", identity_id=ident["id"])
    img = np.full((640, 480, 3), 190, np.uint8)
    cv2.circle(img, (240, 280), 130, (110, 150, 200), -1)
    cv2.imwrite(str(dest), img)
    storage.register(aid=aid, user_id=uid, identity_id=ident["id"], kind="face_photo", path=dest,
                     mime="image/jpeg", width=480, height=640, meta={"view": "frontal"})
    return ident["id"]


def _job_ok(client, auth, job):
    queue.run_pending()
    j = client.get(f"/api/jobs/{job['id']}", headers=auth).json()
    assert j["status"] == "succeeded", j
    return j


def test_generation_requires_declaration_and_instructions(client, auth):
    pid = client.post("/api/projects", json={"name": "X", "aspect": "9:16"}, headers=auth).json()["id"]
    r = client.post(f"/api/projects/{pid}/storyboard/generate", json={}, headers=auth)
    assert r.json()["error"] == "declaracion_pendiente"
    pid = _project(client, auth)
    r = client.post(f"/api/projects/{pid}/storyboard/generate", json={}, headers=auth)
    assert r.json()["error"] == "instrucciones_vacias"
    r = client.patch(f"/api/projects/{pid}", json={"brief": "Un vídeo que parezca real del presidente"}, headers=auth)
    assert r.status_code >= 400
    assert client.post("/api/projects", json={"name": "Y", "style": "inventado"}, headers=auth).status_code >= 400


def test_other_users_cannot_access_project(client, auth):
    pid = _project(client, auth)
    other = client.post("/api/auth/register", json={"email": "otro@example.com", "password": "otra-contraseña"})
    h2 = {"Authorization": f"Bearer {other.json()['token']}"}
    assert client.get(f"/api/projects/{pid}", headers=h2).status_code == 404
    assert client.get(f"/api/projects/{pid}/cast", headers=h2).status_code == 404


def test_full_generation_pipeline(client, auth, env, fake_espeak, tmp_path):
    pid = _project(client, auth)
    iid = _self_identity(client, auth, env, tmp_path)
    voice = client.get("/api/tts/voices", headers=auth).json()[0]["id"]

    # --- reparto: persona real (con consentimiento) y personaje ficticio ---
    ana = client.post(f"/api/projects/{pid}/cast", json={"name": "Ana", "identity_id": iid, "voice_id": voice},
                      headers=auth).json()
    luis = client.post(f"/api/projects/{pid}/cast", json={"name": "Luis", "description": "un robot simpático",
                                                          "style": "comic"}, headers=auth).json()
    assert client.post(f"/api/projects/{pid}/cast", json={"name": "ana"}, headers=auth).status_code == 409
    for c in (ana, luis):
        r = client.post(f"/api/projects/{pid}/cast/{c['id']}/portrait", json={}, headers=auth)
        assert r.status_code == 200 and r.json()["provider"] == "local", r.text
        _job_ok(client, auth, r.json()["job"])
    cast = client.get(f"/api/projects/{pid}/cast", headers=auth).json()
    assert all(c["status"] == "ready" and c["portrait_preview_asset_id"] for c in cast)
    url = client.get(f"/api/assets/{cast[0]['portrait_preview_asset_id']}/url", headers=auth).json()["url"]
    assert client.get(url).status_code == 200
    # El retrato sin aviso visible es interno: no se sirve
    raw = db.one("SELECT portrait_asset_id FROM cast_members WHERE id=?", (ana["id"],))["portrait_asset_id"]
    assert client.get(f"/api/assets/{raw}/url", headers=auth).status_code == 403

    # --- instrucciones y guion (proveedor básico local) ---
    brief = "Ana llega a la playa de Melilla. Luis le grita «¡Hemos ganado!». Los dos bailan al atardecer."
    assert client.patch(f"/api/projects/{pid}", json={"brief": brief}, headers=auth).status_code == 200
    r = client.post(f"/api/projects/{pid}/storyboard/generate", json={"max_scenes": 4}, headers=auth)
    assert r.status_code == 200, r.text
    _job_ok(client, auth, r.json()["job"])
    proj = client.get(f"/api/projects/{pid}", headers=auth).json()
    scenes = proj["storyboard"]["scenes"]
    assert len(scenes) == 3 and scenes[1]["dialogue"][0]["character"] == "Luis"

    # --- edición manual del guion: se queda con dos escenas y se acorta la primera ---
    scenes[0]["duration_seconds"] = 3
    scenes[0]["dialogue"] = [{"character": "Ana", "line": "¡Qué día tan bonito!"}]
    keep = [scenes[0], scenes[1]]
    r = client.put(f"/api/projects/{pid}/storyboard", json={"title": "Victoria", "logline": "", "scenes": keep},
                   headers=auth)
    assert r.status_code == 200 and len(r.json()["scenes"]) == 2, r.text
    s1, s2 = r.json()["scenes"]

    # --- escenas: el clip exige fotograma previo ---
    r = client.post(f"/api/projects/{pid}/scenes/{s1['id']}/animate", json={}, headers=auth)
    assert r.json()["error"] == "fotograma_pendiente"
    for s in (s1, s2):
        _job_ok(client, auth, client.post(f"/api/projects/{pid}/scenes/{s['id']}/keyframe", json={},
                                          headers=auth).json()["job"])
        r = client.post(f"/api/projects/{pid}/scenes/{s['id']}/animate", json={}, headers=auth)
        assert r.status_code == 200 and r.json()["estimate"]["cost_usd"] == 0, r.text
        _job_ok(client, auth, r.json()["job"])
    proj = client.get(f"/api/projects/{pid}", headers=auth).json()
    assert len(proj["clips"]) == 2 and all(c["provider"] == "local" for c in proj["clips"])
    assert [c["source_id"] for c in proj["timeline"]["video"]["clips"]] == \
        [s["clip"]["source_id"] for s in proj["storyboard"]["scenes"]]
    assert "PERSONAJES BASADOS EN PERSONAS REALES" in proj["watermark"]["labels"]
    est = client.get(f"/api/projects/{pid}/generation-estimate", headers=auth).json()
    assert est["pending"] == {"portraits": 0, "keyframes": 0, "clips": 0} and est["cost_usd"] == 0

    # --- voces de la escena: se colocan sobre el clip con subtítulos ---
    r = client.post(f"/api/projects/{pid}/scenes/{s1['id']}/voices", headers=auth)
    assert r.status_code == 200 and len(r.json()["jobs"]) == 1, r.text
    _job_ok(client, auth, r.json()["jobs"][0])
    tl = client.get(f"/api/projects/{pid}", headers=auth).json()["timeline"]
    assert len(tl["dialogue"]["items"]) == 1 and tl["subtitles"]["items"]

    # --- marca de agua: no se puede desactivar ---
    assert client.patch(f"/api/projects/{pid}", json={"watermark": {"enabled": False}}, headers=auth).status_code == 422

    # --- exportación ---
    r = client.post(f"/api/projects/{pid}/export", json={"resolution": "720p", "fps": 25}, headers=auth)
    assert r.json()["error"] == "aviso_pendiente"
    r = client.post(f"/api/projects/{pid}/export", json={"resolution": "720p", "fps": 25, "ack_disclaimer": True},
                    headers=auth)
    res = _job_ok(client, auth, r.json()["job"])["result"]
    out = Path(storage.get(res["render_asset_id"])["path"])
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                            "format=duration:format_tags=comment:stream=width,height", "-of", "default=nw=1", str(out)],
                           capture_output=True, text=True).stdout
    assert "Contenido sintético" in probe and "width=720" in probe and "height=1280" in probe
    dur = float(next(l.split("=")[1] for l in probe.splitlines() if l.startswith("duration=")))
    assert abs(dur - (3 + s2["duration_seconds"])) < 0.3, dur
    assert res.get("srt_asset_id") and res.get("manifest_asset_id")
    side = Path(storage.get(res["manifest_asset_id"])["path"]).read_text(encoding="utf-8")
    assert "VÍDEO GENERADO CON INTELIGENCIA ARTIFICIAL" in side and '"persona_real": true' in side

    # --- eliminar una escena del guion borra su clip y lo quita del montaje ---
    proj = client.get(f"/api/projects/{pid}", headers=auth).json()
    r = client.put(f"/api/projects/{pid}/storyboard",
                   json={"title": "Victoria", "logline": "", "scenes": proj["storyboard"]["scenes"][:1]}, headers=auth)
    assert r.status_code == 200
    proj = client.get(f"/api/projects/{pid}", headers=auth).json()
    assert len(proj["clips"]) == 1 and len(proj["timeline"]["video"]["clips"]) == 1

    # --- retirar el consentimiento elimina retratos, escenas con esa persona y exportaciones ---
    client.post(f"/api/identities/{iid}/revoke", headers=auth)
    proj = client.get(f"/api/projects/{pid}", headers=auth).json()
    ana_now = next(c for c in proj["cast"] if c["name"] == "Ana")
    assert ana_now["status"] == "revoked" and not ana_now["portrait_preview_asset_id"]
    assert proj["clips"] == [] and proj["storyboard"]["scenes"][0]["clip"] is None
    assert client.get(f"/api/assets/{res['render_asset_id']}/url", headers=auth).status_code == 404


def test_paid_providers_require_cost_confirmation(client, auth, env, monkeypatch, tmp_path):
    pid = _project(client, auth)
    c = client.post(f"/api/projects/{pid}/cast", json={"name": "Robo", "description": "robot"}, headers=auth).json()
    monkeypatch.setattr(env, "fal_key", "clave-de-prueba")
    monkeypatch.setattr(env, "image_provider", "auto")
    monkeypatch.setattr(env, "video_provider", "auto")
    r = client.post(f"/api/projects/{pid}/cast/{c['id']}/portrait", json={}, headers=auth)
    assert r.status_code == 402 and r.json()["error"] == "confirmar_coste", r.text
    assert r.json()["details"]["estimate"]["cost_usd"] > 0
    r = client.post(f"/api/projects/{pid}/cast/{c['id']}/portrait", json={"confirm_cost": True}, headers=auth)
    assert r.status_code == 200 and r.json()["provider"] == "fal"
    assert db.one("SELECT COUNT(*) AS n FROM jobs WHERE status='queued'")["n"] == 1  # no se ejecuta: sin red


def test_third_party_consent_link_flow(client, auth):
    ident = client.post("/api/identities", json={"display_name": "Luis", "kind": "third_party",
                                                 "scopes": ["imagen"]}, headers=auth).json()
    req = client.post(f"/api/identities/{ident['id']}/consent-request",
                      json={"purpose": "Vídeo de humor para la boda", "scopes": ["imagen"]}, headers=auth).json()
    token = req["token"]
    info = client.get(f"/api/consent/{token}").json()
    assert info["status"] == "pending" and "Vídeo de humor" in info["statement"]
    pid = _project(client, auth)
    # Sin consentimiento otorgado no puede usarse como personaje
    r = client.post(f"/api/projects/{pid}/cast", json={"name": "Luis", "identity_id": ident["id"]}, headers=auth)
    assert r.status_code == 403
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


def test_project_management_and_account_deletion(client, auth, env):
    pid = _project(client, auth)
    c = client.post(f"/api/projects/{pid}/cast", json={"name": "Robo"}, headers=auth).json()
    _job_ok(client, auth, client.post(f"/api/projects/{pid}/cast/{c['id']}/portrait", json={},
                                      headers=auth).json()["job"])
    summary = client.get("/api/projects", headers=auth).json()[0]
    assert summary["cast"] == 1 and summary["storage_bytes"] > 0 and summary["expires_at"] and summary["thumb_asset_id"]
    dup = client.post(f"/api/projects/{pid}/duplicate", headers=auth).json()
    assert dup["use_declaration"] and dup["cast"] == [] and dup["style"] == "caricatura"
    user_dirs = list(env.storage_dir.iterdir())
    assert client.delete("/api/auth/account", headers=auth).status_code == 200
    assert not any(d.exists() for d in user_dirs)
    assert client.get("/api/projects", headers=auth).status_code == 401
