from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, Form, UploadFile
from pydantic import BaseModel

from .. import db, policy, services, storage
from ..ai import tts
from ..config import settings
from ..errors import AppError, NotFound
from ..jobs import queue
from .deps import current_user

router = APIRouter(prefix="/api", tags=["diálogos"])

VOICE_OWNERS = {"sintetica", "yo", "tercero"}


class Estimate(BaseModel):
    text: str
    speed: float = 1.0


class TTSDialogue(BaseModel):
    text: str
    voice_id: str
    language: str = "es-ES"
    speed: float = 1.0
    pitch: float = 0.0
    gain_db: float = 0.0
    target_duration: float | None = None
    label: str | None = None


def _out(d: dict[str, Any]) -> dict[str, Any]:
    return {"id": d["id"], "mode": d["mode"], "text": d["text"], "settings": db.jload(d["settings"], {}),
            "audio_asset_id": d["audio_asset_id"], "duration": d["duration"],
            "estimated_duration": d["estimated_duration"], "segments": db.jload(d["segments"], []),
            "subtitles": db.jload(d["subtitles"], []), "created_at": d["created_at"]}


@router.get("/tts/voices")
def voices(user=Depends(current_user)) -> list[dict[str, Any]]:
    return [v.as_dict() for v in tts.list_voices()]


@router.post("/tts/estimate")
def estimate(body: Estimate, user=Depends(current_user)) -> dict[str, Any]:
    if not (0.5 <= body.speed <= 2.0):
        raise AppError("La velocidad debe estar entre 0,5 y 2,0.")
    return tts.estimate_duration(body.text, body.speed)


@router.get("/projects/{pid}/dialogues")
def list_dialogues(pid: str, user=Depends(current_user)) -> list[dict[str, Any]]:
    services.load_project(pid, user["id"])
    return [_out(d) for d in db.all_("SELECT * FROM dialogues WHERE project_id=? ORDER BY created_at", (pid,))]


def _validate_common(speed: float, pitch: float, gain_db: float) -> None:
    if not (0.5 <= speed <= 2.0):
        raise AppError("La velocidad debe estar entre 0,5 y 2,0.")
    if not (-6 <= pitch <= 6):
        raise AppError("El tono debe estar entre -6 y +6 semitonos.")
    if not (-20 <= gain_db <= 12):
        raise AppError("El volumen debe estar entre -20 y +12 dB.")


@router.post("/projects/{pid}/dialogues/tts")
def create_tts(pid: str, body: TTSDialogue, user=Depends(current_user)) -> dict[str, Any]:
    services.load_project(pid, user["id"])
    text = body.text.strip()
    if not text:
        raise AppError("Escriba el texto del diálogo.")
    if len(text) > 2000:
        raise AppError("El diálogo no puede superar 2000 caracteres.")
    policy.check_dialogue_text(text)
    _validate_common(body.speed, body.pitch, body.gain_db)
    voice = tts.get_voice(body.voice_id)
    est = tts.estimate_duration(text, body.speed)
    if est["seconds"] > settings.max_dialogue_seconds:
        raise AppError(f"El diálogo estimado ({est['seconds']:.0f} s) supera el máximo de "
                       f"{settings.max_dialogue_seconds} s.")
    did = db.new_id()
    s = {"voice_id": voice.id, "voice_name": voice.name, "language": body.language, "speed": body.speed,
         "pitch": body.pitch, "gain_db": body.gain_db, "target_duration": body.target_duration,
         "voice_owner": "sintetica", "label": (body.label or "")[:60]}
    db.execute("""INSERT INTO dialogues(id,project_id,mode,text,settings,estimated_duration,created_at)
                  VALUES (?,?,?,?,?,?,?)""", (did, pid, "tts", text, db.jdump(s), est["seconds"], db.now()))
    job = queue.enqueue("tts", user["id"], pid, {"dialogue_id": did}, message="Generando voz")
    return {"dialogue": _out(db.one("SELECT * FROM dialogues WHERE id=?", (did,))), "job": job}


@router.put("/projects/{pid}/dialogues/{did}/tts")
def regenerate_tts(pid: str, did: str, body: TTSDialogue, user=Depends(current_user)) -> dict[str, Any]:
    services.load_project(pid, user["id"])
    d = db.one("SELECT * FROM dialogues WHERE id=? AND project_id=? AND mode='tts'", (did, pid))
    if not d:
        raise NotFound("Diálogo no encontrado.")
    text = body.text.strip()
    policy.check_dialogue_text(text)
    _validate_common(body.speed, body.pitch, body.gain_db)
    voice = tts.get_voice(body.voice_id)
    s = db.jload(d["settings"], {}) | {"voice_id": voice.id, "voice_name": voice.name, "language": body.language,
                                       "speed": body.speed, "pitch": body.pitch, "gain_db": body.gain_db,
                                       "target_duration": body.target_duration}
    est = tts.estimate_duration(text, body.speed)
    db.execute("UPDATE dialogues SET text=?, settings=?, estimated_duration=? WHERE id=?",
               (text, db.jdump(s), est["seconds"], did))
    job = queue.enqueue("tts", user["id"], pid, {"dialogue_id": did}, message="Regenerando voz")
    return {"dialogue": _out(db.one("SELECT * FROM dialogues WHERE id=?", (did,))), "job": job}


@router.post("/projects/{pid}/dialogues/audio")
def create_from_audio(pid: str, file: UploadFile = File(...), mode: str = Form("file"),
                      text: str = Form(""), voice_owner: str = Form("yo"),
                      voice_identity_id: str = Form(""), speed: float = Form(1.0), pitch: float = Form(0.0),
                      gain_db: float = Form(0.0), language: str = Form("es-ES"),
                      user=Depends(current_user)) -> dict[str, Any]:
    """Modalidades B (grabación con micrófono) y C (archivo de audio)."""
    services.load_project(pid, user["id"])
    if mode not in ("mic", "file"):
        raise AppError("Modalidad no válida.")
    if voice_owner not in VOICE_OWNERS:
        raise AppError("Indique de quién es la voz.")
    if voice_owner == "tercero":
        if not voice_identity_id:
            raise AppError("La voz es de otra persona: seleccione su identidad con consentimiento de voz.")
        policy.require_identity_usable(services.load_identity(voice_identity_id, user["id"]), "voz")
    text = text.strip()
    if text:
        policy.check_dialogue_text(text)
    _validate_common(speed, pitch, gain_db)
    ext = Path(file.filename or "audio.webm").suffix.lower() or ".webm"
    if ext not in {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac", ".webm", ".mp4", ".caf", ".opus"}:
        raise AppError("Formato de audio no admitido.")
    aid, dest = storage.new_path(user["id"], ext, project_id=pid)
    storage.save_stream(file.file, dest, max_bytes=100 * 1024 * 1024)
    raw = storage.register(aid=aid, user_id=user["id"], project_id=pid, kind="audio_raw", path=dest,
                           mime=file.content_type)
    did = db.new_id()
    s = {"voice_owner": voice_owner, "voice_identity_id": voice_identity_id or None, "speed": speed,
         "pitch": pitch, "gain_db": gain_db, "language": language}
    db.execute("""INSERT INTO dialogues(id,project_id,mode,text,settings,created_at) VALUES (?,?,?,?,?,?)""",
               (did, pid, mode, text, db.jdump(s), db.now()))
    db.audit("dialogo_audio", user["id"], pid, dialogue=did, mode=mode, voice_owner=voice_owner)
    job = queue.enqueue("audio_import", user["id"], pid, {"dialogue_id": did, "raw_asset_id": raw["id"]},
                        message="Procesando audio")
    return {"dialogue": _out(db.one("SELECT * FROM dialogues WHERE id=?", (did,))), "job": job}


class SubtitlesIn(BaseModel):
    subtitles: list[dict[str, Any]]


@router.put("/projects/{pid}/dialogues/{did}/subtitles")
def edit_subtitles(pid: str, did: str, body: SubtitlesIn, user=Depends(current_user)) -> dict[str, Any]:
    services.load_project(pid, user["id"])
    d = db.one("SELECT * FROM dialogues WHERE id=? AND project_id=?", (did, pid))
    if not d:
        raise NotFound("Diálogo no encontrado.")
    clean = []
    for s in body.subtitles:
        text = str(s.get("text", "")).strip()[:300]
        st, en = float(s.get("start", 0)), float(s.get("end", 0))
        if text and en > st >= 0:
            clean.append({"start": round(st, 3), "end": round(en, 3), "text": text})
    db.execute("UPDATE dialogues SET subtitles=? WHERE id=?", (db.jdump(clean), did))
    return _out(db.one("SELECT * FROM dialogues WHERE id=?", (did,)))


@router.delete("/projects/{pid}/dialogues/{did}")
def delete_dialogue(pid: str, did: str, user=Depends(current_user)) -> dict[str, bool]:
    p = services.load_project(pid, user["id"])
    d = db.one("SELECT * FROM dialogues WHERE id=? AND project_id=?", (did, pid))
    if not d:
        raise NotFound("Diálogo no encontrado.")
    if d["audio_asset_id"] and any(it.get("dialogue_id") == did for it in p["timeline"]["dialogue"]["items"]):
        raise AppError("El diálogo está colocado en la línea de tiempo; quítelo del editor antes de borrarlo.")
    if d["audio_asset_id"]:
        storage.delete_asset(d["audio_asset_id"])
    db.execute("DELETE FROM dialogues WHERE id=?", (did,))
    return {"ok": True}


class FitIn(BaseModel):
    text: str
    speed: float = 1.0
    available: float


@router.post("/projects/{pid}/dialogues/fit")
def fit_dialogue(pid: str, body: FitIn, user=Depends(current_user)) -> dict[str, Any]:
    """Comprueba si el texto cabe en la duración de la escena y propone alternativas si no cabe."""
    import re as _re
    services.load_project(pid, user["id"])
    est = tts.estimate_duration(body.text, body.speed)
    available = max(0.0, round(body.available, 2))
    fits = est["seconds"] <= available
    suggestions: list[dict[str, Any]] = []
    if not fits and available > 0:
        factor = est["seconds"] / available
        if factor <= 1.25:
            suggestions.append({"type": "acelerar", "speed": round(min(2.0, body.speed * factor + 0.02), 2),
                                "text": f"Acelerar la voz un {factor - 1:.0%} (por debajo del 25 % suena natural)."})
        suggestions.append({"type": "ampliar", "seconds": int(min(10, -(-est["seconds"] // 1))),
                            "text": f"Ampliar la escena a {min(10, -(-est['seconds'] // 1)):.0f} s."})
        words = len(body.text.split())
        keep = max(1, int(words * available / est["seconds"]))
        suggestions.append({"type": "recortar", "max_words": keep,
                            "text": f"Recortar el texto a unas {keep} palabras (ahora {words})."})
        sentences = [s for s in _re.split(r"(?<=[.!?…])\s+", body.text.strip()) if s]
        if len(sentences) > 1:
            suggestions.append({"type": "dividir", "takes": sentences,
                                "text": f"Dividir el diálogo en {len(sentences)} escenas o tomas."})
    return {"estimated": est["seconds"], "available": available, "fits": fits, "suggestions": suggestions}
