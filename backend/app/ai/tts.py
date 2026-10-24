"""Síntesis de voz.

Proveedores gratuitos y locales (sólo voces de catálogo, NO clonación de voz):

* ``espeak`` — eSpeak NG (GPL-3.0). Local y gratuito; voz robótica, útil
  para pruebas y maquetas de ritmo.
* ``piper`` — Piper (motor GPL-3.0 en su versión actual «piper1-gpl»; cada
  voz tiene su propia licencia en su MODEL_CARD). Local y gratuito, calidad
  aceptable en CPU. Las voces se descargan aparte a PIPER_VOICES_DIR.
"""
from __future__ import annotations

import json
import re
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..config import settings
from ..errors import AppError, ProviderUnavailable
from ..media import ffmpeg
from ..media.subtitles import syllables

PAUSE_RE = re.compile(r"\[pausa(?:\s+(\d+(?:[.,]\d+)?))?\]", re.IGNORECASE)
DEFAULT_PAUSE = 0.5
SYLLABLES_PER_SECOND = 6.0  # habla conversacional en español a velocidad 1,0


@dataclass
class Voice:
    id: str
    provider: str
    name: str
    language: str
    paid: bool
    license: str

    def as_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


def parse_script(text: str) -> list[dict[str, Any]]:
    """Separa el texto en tramos hablados y pausas explícitas ``[pausa 0.8]``."""
    parts: list[dict[str, Any]] = []
    pos = 0
    for m in PAUSE_RE.finditer(text):
        chunk = text[pos:m.start()].strip()
        if chunk:
            parts.append({"type": "speech", "text": chunk})
        secs = float(m.group(1).replace(",", ".")) if m.group(1) else DEFAULT_PAUSE
        parts.append({"type": "pause", "seconds": min(5.0, max(0.05, secs))})
        pos = m.end()
    tail = text[pos:].strip()
    if tail:
        parts.append({"type": "speech", "text": tail})
    return parts


def apply_pronunciations(text: str, rules: dict[str, str]) -> str:
    for word, spoken in (rules or {}).items():
        if word.strip():
            text = re.sub(rf"\b{re.escape(word.strip())}\b", spoken, text, flags=re.IGNORECASE)
    return text


def estimate_duration(text: str, speed: float = 1.0) -> dict[str, Any]:
    parts = parse_script(text)
    speech = 0.0
    pauses = 0.0
    for p in parts:
        if p["type"] == "pause":
            pauses += p["seconds"]
        else:
            speech += syllables(p["text"]) / (SYLLABLES_PER_SECOND * max(0.25, speed))
            pauses += 0.25 * len(re.findall(r"[,;:]", p["text"])) / max(0.25, speed)
            pauses += 0.45 * len(re.findall(r"[.!?…]", p["text"])) / max(0.25, speed)
    return {"seconds": round(speech + pauses, 2), "speech_seconds": round(speech, 2),
            "pause_seconds": round(pauses, 2), "syllables": sum(syllables(p.get("text", "")) for p in parts)}


# ---------------------------------------------------------------- catálogo

def _espeak_voices() -> list[Voice]:
    if not settings.espeak_binary:
        return []
    lic = "eSpeak NG (GPL-3.0). Voz sintética de catálogo."
    return [Voice("espeak:es", "espeak", "eSpeak · Español (España)", "es-ES", False, lic),
            Voice("espeak:es-419", "espeak", "eSpeak · Español (Latinoamérica)", "es-419", False, lic),
            Voice("espeak:en", "espeak", "eSpeak · Inglés", "en", False, lic)]


def _piper_voices() -> list[Voice]:
    if not settings.piper_command or not settings.piper_voices_dir:
        return []
    out = []
    for model in sorted(Path(settings.piper_voices_dir).glob("*.onnx")):
        meta_path = model.with_suffix(".onnx.json")
        lang = "?"
        if meta_path.is_file():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                lang = (meta.get("language") or {}).get("code", "?")
            except Exception:
                pass
        out.append(Voice(f"piper:{model.stem}", "piper", f"Piper · {model.stem}", lang.replace("_", "-"), False,
                         "Piper (GPL-3.0). Licencia de la voz: ver su MODEL_CARD."))
    return out


def list_voices() -> list[Voice]:
    return _espeak_voices() + _piper_voices()


def get_voice(voice_id: str) -> Voice:
    for v in list_voices():
        if v.id == voice_id:
            return v
    raise ProviderUnavailable("La voz seleccionada no está disponible en este servidor.")


# ---------------------------------------------------------------- síntesis

def synthesize_piece(voice: Voice, text: str, out_wav: Path, *, speed: float, pitch: float) -> float:
    """Sintetiza un tramo y devuelve la velocidad que queda pendiente de aplicar con FFmpeg."""
    kind, vid = voice.id.split(":", 1)
    raw = out_wav.with_suffix(".raw.wav")
    remaining = speed
    if kind == "espeak":
        wpm = int(max(80, min(450, 165 * speed)))
        pitch_val = int(max(0, min(99, 50 + pitch * 4)))
        proc = subprocess.run([settings.espeak_binary, "-v", vid, "-s", str(wpm), "-p", str(pitch_val),
                               "-w", str(raw), "--", text], capture_output=True, text=True, timeout=120)
        if proc.returncode != 0:
            raise AppError("eSpeak no pudo sintetizar el texto.", code="tts")
        remaining = 1.0
        pitch = 0.0
    elif kind == "piper":
        model = Path(settings.piper_voices_dir) / f"{vid}.onnx"
        length_scale = round(1.0 / max(0.5, min(2.0, speed)), 3)
        template = settings.piper_command
        if "{" not in template:
            template = f"{shlex.quote(template)} --model {{model}} --output_file {{output}} --length_scale {{length_scale}}"
        args = [a.replace("{model}", str(model)).replace("{output}", str(raw))
                .replace("{length_scale}", str(length_scale)) for a in shlex.split(template)]
        proc = subprocess.run(args, input=text, capture_output=True, text=True, timeout=300)
        if proc.returncode != 0 or not raw.exists():
            raise AppError("Piper no pudo sintetizar el texto: " + proc.stderr.strip()[-200:], code="tts")
        remaining = speed / (1.0 / length_scale)
    else:
        raise ProviderUnavailable("Proveedor de voz desconocido.")
    ffmpeg.process_audio(raw, out_wav, tempo=remaining, semitones=pitch)
    raw.unlink(missing_ok=True)
    return ffmpeg.probe(out_wav).duration
