"""Guion gráfico: convierte las instrucciones del usuario en escenas con prompts de imagen y vídeo.

* ``anthropic``: Claude mediante el SDK oficial con salida estructurada (JSON Schema). Se activa
  el parámetro de servidor ``fallbacks: "default"``: si los filtros de seguridad rechazan la
  petición en un modelo, la API la reintenta en el modelo de respaldo recomendado.
* ``basic``: alternativa sin IA (una escena por frase, diálogos entre comillas). Útil sin clave.
"""
from __future__ import annotations

import json
import os
import re
import uuid
from typing import Any

from ..config import settings
from ..errors import AppError, PolicyError, ProviderUnavailable, RetryableError

SCENE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["title", "description", "setting", "characters", "camera", "duration_seconds", "dialogue",
                 "keyframe_prompt", "video_prompt"],
    "properties": {
        "title": {"type": "string"},
        "description": {"type": "string"},
        "setting": {"type": "string"},
        "characters": {"type": "array", "items": {"type": "string"}},
        "camera": {"type": "string"},
        "duration_seconds": {"type": "integer"},
        "dialogue": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["character", "line"],
            "properties": {"character": {"type": "string"}, "line": {"type": "string"}}}},
        "keyframe_prompt": {"type": "string"},
        "video_prompt": {"type": "string"},
    },
}

STORYBOARD_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["refused", "refusal_reason", "title", "logline", "scenes"],
    "properties": {
        "refused": {"type": "boolean"},
        "refusal_reason": {"type": "string"},
        "title": {"type": "string"},
        "logline": {"type": "string"},
        "scenes": {"type": "array", "items": SCENE_SCHEMA},
    },
}

SYSTEM = """Eres director y guionista de vídeos cortos generados con IA. Conviertes las instrucciones del usuario en un guion gráfico para generar cada escena con un modelo de imagen (fotograma clave) y después con un modelo imagen→vídeo.

Reglas:
- Usa SOLO los personajes del reparto, con su nombre exacto. No añadas personas reales ni famosas, ni con nombre ni por descripción reconocible. Los extras anónimos de fondo están permitidos.
- Escribe en español: title, description, setting, camera y dialogue. Escribe en inglés keyframe_prompt y video_prompt, que leen los modelos de imagen y vídeo.
- keyframe_prompt describe un único fotograma fijo: lugar, posición y expresión de cada personaje (por su nombre), vestuario, luz y encuadre. No incluyas el estilo artístico, que se añade después. Sin texto ni rótulos en la imagen.
- video_prompt describe sólo el movimiento a partir de ese fotograma: acciones, gestos y movimiento de cámara. Es un plano continuo, sin cortes.
- duration_seconds entre 3 y 10. Los diálogos deben caber en la escena: unas 2,5 palabras por segundo como máximo.
- Respeta el número máximo de escenas indicado y el formato de imagen.
- Pon refused a true y explica el motivo en refusal_reason (dejando scenes vacío) si la petición pide contenido sexual o íntimo, violencia gráfica, acoso o humillación de una persona, suplantación para engañar o estafar, desinformación sobre hechos o personas reales, propaganda política engañosa, menores en cualquier situación inapropiada o personas famosas reales. En caso contrario, refused es false y refusal_reason una cadena vacía."""


def provider() -> str:
    p = settings.llm_provider
    if p in ("anthropic", "basic"):
        return p
    return "anthropic" if (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")) else "basic"


def status() -> dict[str, Any]:
    p = provider()
    if p == "anthropic":
        try:
            import anthropic  # noqa: F401
        except ImportError:
            return {"provider": "anthropic", "available": False, "reason": "Falta el paquete «anthropic»."}
        return {"provider": "anthropic", "available": True, "model": settings.anthropic_model, "paid": True}
    return {"provider": "basic", "available": True, "paid": False,
            "note": "Guion básico por reglas. Configure ANTHROPIC_API_KEY para que Claude escriba el guion y los prompts."}


def _cast_text(cast: list[dict[str, Any]]) -> str:
    lines = []
    for c in cast:
        kind = "basado en una persona real con su consentimiento" if c.get("identity_id") else "personaje ficticio"
        lines.append(f"- {c['name']} ({kind}). {c.get('description', '')}".strip())
    return "\n".join(lines) or "- (sin personajes: sólo escenarios o extras anónimos)"


def _call_claude(system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
    import anthropic
    client = anthropic.Anthropic()
    try:
        resp = client.beta.messages.create(
            model=settings.anthropic_model,
            max_tokens=16000,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            output_config={"effort": "medium", "format": {"type": "json_schema", "schema": schema}},
            system=system,
            messages=[{"role": "user", "content": user}],
        )
    except anthropic.AuthenticationError:
        raise ProviderUnavailable("La clave de la API de Anthropic no es válida.")
    except anthropic.PermissionDeniedError:
        raise ProviderUnavailable("La clave de la API de Anthropic no tiene permiso para este modelo.")
    except anthropic.BadRequestError as e:
        raise AppError(f"Petición rechazada por la API de Claude: {e.message}", code="llm")
    except anthropic.RateLimitError:
        raise RetryableError("Límite de uso de la API de Claude alcanzado; se reintentará.")
    except anthropic.APIStatusError as e:
        if e.status_code >= 500:
            raise RetryableError("La API de Claude no está disponible ahora; se reintentará.")
        raise AppError(f"Error de la API de Claude ({e.status_code}).", code="llm")
    except anthropic.APIConnectionError:
        raise RetryableError("No se pudo conectar con la API de Claude.")
    if resp.stop_reason == "refusal":
        raise PolicyError("La IA ha rechazado la petición por sus políticas de seguridad. Reformule las instrucciones.",
                          code="instrucciones_rechazadas")
    if resp.stop_reason == "max_tokens":
        raise AppError("El guion es demasiado largo. Reduzca el número de escenas.", code="llm")
    text = next((b.text for b in resp.content if b.type == "text"), "")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        raise RetryableError("La respuesta de Claude no tenía el formato esperado; se reintentará.")


def storyboard(brief: str, cast: list[dict[str, Any]], *, style_label: str, aspect: str,
               max_scenes: int, target_seconds: int | None = None) -> dict[str, Any]:
    if provider() == "basic":
        return basic_storyboard(brief, cast, max_scenes)
    user = (f"Reparto:\n{_cast_text(cast)}\n\nEstilo visual elegido: {style_label}\nFormato: {aspect}\n"
            f"Máximo de escenas: {max_scenes}\n"
            + (f"Duración total aproximada: {target_seconds} s\n" if target_seconds else "")
            + f"\nInstrucciones del usuario:\n<instrucciones>\n{brief}\n</instrucciones>")
    data = _call_claude(SYSTEM, user, STORYBOARD_SCHEMA)
    if data.get("refused"):
        raise PolicyError(f"No se puede crear este vídeo: {data.get('refusal_reason') or 'contenido no permitido'}.",
                          code="instrucciones_rechazadas")
    data["provider"] = "anthropic"
    data["model"] = settings.anthropic_model
    return data


def rewrite_scene(board: dict[str, Any], index: int, instruction: str, cast: list[dict[str, Any]], *,
                  style_label: str, aspect: str) -> dict[str, Any]:
    scene = board["scenes"][index]
    if provider() == "basic":
        new = basic_storyboard(instruction or scene["description"], cast, 1)["scenes"][0]
        return new
    user = (f"Reparto:\n{_cast_text(cast)}\n\nEstilo: {style_label}. Formato: {aspect}.\n"
            f"Guion completo actual (JSON):\n{json.dumps(board.get('scenes', []), ensure_ascii=False)}\n\n"
            f"Reescribe SOLO la escena número {index + 1} siguiendo esta indicación del usuario:\n"
            f"<indicacion>\n{instruction}\n</indicacion>\nDevuelve únicamente esa escena.")
    return _call_claude(SYSTEM + "\nDevuelve una sola escena con el esquema indicado.", user, SCENE_SCHEMA)


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
