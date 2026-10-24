"""Guion gráfico: convierte las instrucciones del usuario en escenas con sus prompts, gratis y en local.

Guion por reglas (sin IA): una escena por frase, personajes detectados por su nombre y diálogos entre
comillas «…». El proveedor es intercambiable para conectar en el futuro un modelo de lenguaje abierto
ejecutado en local (p. ej. con Ollama).
"""
from __future__ import annotations

import re
import uuid
from typing import Any

from ..errors import AppError


def provider() -> str:
    return "basic"


def status() -> dict[str, Any]:
    return {"provider": "basic", "available": True,
            "note": "Guion por reglas: una escena por frase y diálogos entre comillas «…»."}


def storyboard(brief: str, cast: list[dict[str, Any]], *, style_label: str, aspect: str, max_scenes: int,
               target_seconds: int | None = None) -> dict[str, Any]:
    board = basic_storyboard(brief, cast, max_scenes)
    if target_seconds and board["scenes"]:
        per = max(3, min(10, round(target_seconds / len(board["scenes"]))))
        for sc in board["scenes"]:
            sc["duration_seconds"] = per
    return board


def rewrite_scene(board: dict[str, Any], index: int, instruction: str, cast: list[dict[str, Any]], *,
                  style_label: str, aspect: str) -> dict[str, Any]:
    scene = board["scenes"][index]
    return basic_storyboard(instruction or scene["description"], cast, 1)["scenes"][0]


# ------------------------------------------------------------------ guion básico (sin IA)

QUOTE = re.compile(r"[«\"“]([^»\"”]{2,200})[»\"”]")


def basic_storyboard(brief: str, cast: list[dict[str, Any]], max_scenes: int) -> dict[str, Any]:
    text = re.sub(r"\s+", " ", brief).strip()
    sentences = [s.strip() for s in re.split(r"(?<=[.!?…])\s+", text) if len(s.strip()) > 3][:max_scenes]
    if not sentences:
        raise AppError("Escriba lo que debe ocurrir en el vídeo.", code="instrucciones_vacias")
    names = [c["name"] for c in cast]
    cameras = ["Plano general con acercamiento lento", "Plano medio con desplazamiento lateral",
               "Primer plano con acercamiento", "Plano general con alejamiento"]
    scenes = []
    for i, s in enumerate(sentences):
        present = [n for n in names if re.search(rf"\b{re.escape(n)}\b", s, re.IGNORECASE)]
        if not present and len(names) <= 2:
            present = names
        speaker = present[0] if present else (names[0] if names else "Narrador")
        dialogue = [{"character": speaker, "line": q.strip()} for q in QUOTE.findall(s)]
        clean = QUOTE.sub("", s).strip(" ,.")
        scenes.append({
            "title": f"Escena {i + 1}",
            "description": clean or s,
            "setting": clean[:80] or s[:80],
            "characters": present,
            "camera": cameras[i % len(cameras)],
            "duration_seconds": 5,
            "dialogue": dialogue,
            "keyframe_prompt": clean or s,
            "video_prompt": f"Natural, gentle movement. {clean or s}",
        })
    return {"refused": False, "refusal_reason": "", "title": sentences[0][:60], "logline": text[:200],
            "scenes": scenes, "provider": "basic"}


def normalize(board: dict[str, Any], cast: list[dict[str, Any]], max_scenes: int) -> dict[str, Any]:
    """Valida y limpia el guion (también el editado por el usuario)."""
    names = {c["name"] for c in cast}
    scenes = []
    for i, s in enumerate((board.get("scenes") or [])[:max_scenes]):
        chars = [c for c in s.get("characters", []) if c in names]
        dur = int(max(3, min(10, int(s.get("duration_seconds") or 5))))
        dialogue = [{"character": d.get("character", "")[:60], "line": str(d.get("line", "")).strip()[:300]}
                    for d in s.get("dialogue", []) if str(d.get("line", "")).strip()]
        scenes.append({
            "id": s.get("id") or uuid.uuid4().hex[:10],
            "title": str(s.get("title") or f"Escena {i + 1}")[:120],
            "description": str(s.get("description", ""))[:1500],
            "setting": str(s.get("setting", ""))[:300],
            "characters": chars,
            "camera": str(s.get("camera", ""))[:200],
            "duration_seconds": dur,
            "dialogue": dialogue[:8],
            "keyframe_prompt": str(s.get("keyframe_prompt", ""))[:2000],
            "video_prompt": str(s.get("video_prompt", ""))[:1500],
            "keyframe": s.get("keyframe"),
            "clip": s.get("clip"),
        })
    return {"title": str(board.get("title", ""))[:120], "logline": str(board.get("logline", ""))[:500],
            "provider": board.get("provider"), "model": board.get("model"), "scenes": scenes}
