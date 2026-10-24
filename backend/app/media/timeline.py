"""Modelo de la línea de tiempo multipista.

Dos bases temporales:

* **Tiempo de origen**: segundos del vídeo principal del proyecto. Los
  diálogos sincronizados y los silenciados del audio original se expresan en
  esta base, porque están ligados a la imagen y deben seguirla aunque se
  recorten o reordenen las escenas.
* **Tiempo de salida**: segundos del vídeo final. La música, el audio
  adicional y los gráficos se expresan en esta base.

Estructura::

    {
      "video": {"clips": [{"id", "in", "out"}]},             # secuencia
      "source_audio": {"volume": 1.0, "mutes": [{"start","end"}]},
      "dialogue": {"volume": 1.0, "items": [{"id","dialogue_id","asset_id","source_start","volume"}]},
      "tracks": [{"id","name","kind":"audio|music","volume","muted",
                  "clips":[{"id","asset_id","start","in","out","volume"}]}],
      "subtitles": {"enabled": true, "style": {...},
                    "items": [{"id","start","end","text","base":"source|output"}]},
      "graphics": [{"id","start","end","text","x","y","size","color"}],
      "fit": "contain_blur|contain|cover"
    }
"""
from __future__ import annotations

import uuid
from typing import Any

from ..errors import AppError

FITS = {"contain_blur", "contain", "cover"}


def _id() -> str:
    return uuid.uuid4().hex[:10]


def empty(duration: float = 0.0) -> dict[str, Any]:
    return {
        "video": {"clips": [{"id": _id(), "in": 0.0, "out": round(duration, 3)}] if duration else []},
        "source_audio": {"volume": 1.0, "mutes": []},
        "dialogue": {"volume": 1.0, "items": []},
        "tracks": [
            {"id": "voz_extra", "name": "Audio", "kind": "audio", "volume": 1.0, "muted": False, "clips": []},
            {"id": "musica", "name": "Música", "kind": "music", "volume": 0.6, "muted": False, "clips": []},
        ],
        "subtitles": {"enabled": True, "style": {"size": 0.05, "margin": 0.14}, "items": []},
        "graphics": [],
        "fit": "contain_blur",
    }


def _num(v: Any, name: str, lo: float | None = None, hi: float | None = None) -> float:
    try:
        f = float(v)
    except (TypeError, ValueError):
        raise AppError(f"Valor numérico no válido en «{name}».", code="linea_de_tiempo")
    if lo is not None and f < lo:
        raise AppError(f"«{name}» no puede ser menor que {lo}.", code="linea_de_tiempo")
    if hi is not None and f > hi:
        raise AppError(f"«{name}» no puede ser mayor que {hi}.", code="linea_de_tiempo")
    return round(f, 3)


def validate(tl: dict[str, Any], source_duration: float, known_assets: set[str]) -> dict[str, Any]:
    """Valida y normaliza una línea de tiempo recibida del cliente."""
    base = empty()
    out: dict[str, Any] = {}
    clips = []
    for c in (tl.get("video") or {}).get("clips", []):
        a = _num(c.get("in"), "inicio de escena", 0, source_duration + 0.01)
        b = _num(c.get("out"), "fin de escena", 0, source_duration + 0.01)
        if b - a < 0.1:
            raise AppError("Cada escena debe durar al menos 0,1 s.", code="linea_de_tiempo")
        clips.append({"id": str(c.get("id") or _id()), "in": a, "out": min(b, round(source_duration, 3))})
    out["video"] = {"clips": clips}

    sa = tl.get("source_audio") or {}
    out["source_audio"] = {
        "volume": _num(sa.get("volume", 1.0), "volumen original", 0, 2),
        "mutes": [{"start": _num(m["start"], "silencio"), "end": _num(m["end"], "silencio")}
                  for m in sa.get("mutes", []) if float(m["end"]) > float(m["start"])],
    }
    dl = tl.get("dialogue") or {}
    items = []
    for it in dl.get("items", []):
        if it.get("asset_id") not in known_assets:
            raise AppError("Un diálogo hace referencia a un audio inexistente.", code="linea_de_tiempo")
        items.append({
            "id": str(it.get("id") or _id()),
            "dialogue_id": it.get("dialogue_id"),
            "asset_id": it["asset_id"],
            "source_start": _num(it.get("source_start", 0), "inicio del diálogo", 0, source_duration),
            "volume": _num(it.get("volume", 1.0), "volumen del diálogo", 0, 2),
        })
    out["dialogue"] = {"volume": _num(dl.get("volume", 1.0), "volumen de diálogos", 0, 2), "items": items}

    tracks = []
    for t in tl.get("tracks") or base["tracks"]:
        tclips = []
        for c in t.get("clips", []):
            if c.get("asset_id") not in known_assets:
                raise AppError("Una pista hace referencia a un audio inexistente.", code="linea_de_tiempo")
            a = _num(c.get("in", 0), "recorte de audio", 0)
            b = _num(c.get("out", 0), "recorte de audio", 0)
            if b <= a:
                continue
            tclips.append({"id": str(c.get("id") or _id()), "asset_id": c["asset_id"],
                           "start": _num(c.get("start", 0), "posición", 0), "in": a, "out": b,
                           "volume": _num(c.get("volume", 1.0), "volumen", 0, 2)})
        kind = t.get("kind", "audio")
        tracks.append({"id": str(t.get("id") or _id()), "name": str(t.get("name") or "Pista")[:40],
                       "kind": kind if kind in ("audio", "music") else "audio",
                       "volume": _num(t.get("volume", 1.0), "volumen de pista", 0, 2),
                       "muted": bool(t.get("muted", False)), "clips": tclips})
    out["tracks"] = tracks

    st = tl.get("subtitles") or {}
    style = st.get("style") or {}
    out["subtitles"] = {
        "enabled": bool(st.get("enabled", True)),
        "style": {"size": _num(style.get("size", 0.05), "tamaño de subtítulos", 0.02, 0.1),
                  "margin": _num(style.get("margin", 0.14), "margen de subtítulos", 0.02, 0.4)},
        "items": [],
    }
    for it in st.get("items", []):
        text = str(it.get("text", "")).strip()[:300]
        if not text:
            continue
        s, e = _num(it.get("start"), "subtítulo", 0), _num(it.get("end"), "subtítulo", 0)
        if e <= s:
            continue
        item = {"id": str(it.get("id") or _id()), "start": s, "end": e, "text": text,
                "base": "source" if it.get("base") == "source" else "output"}
        if it.get("dialogue_id"):
            item["dialogue_id"] = str(it["dialogue_id"])
        out["subtitles"]["items"].append(item)
    gfx = []
    for g in tl.get("graphics", []):
        text = str(g.get("text", "")).strip()[:120]
        if not text:
            continue
        gfx.append({"id": str(g.get("id") or _id()), "text": text,
                    "start": _num(g.get("start", 0), "gráfico", 0), "end": _num(g.get("end", 0), "gráfico", 0),
                    "x": _num(g.get("x", 0.5), "posición X", 0, 1), "y": _num(g.get("y", 0.2), "posición Y", 0, 1),
                    "size": _num(g.get("size", 0.06), "tamaño", 0.02, 0.2),
                    "color": g.get("color", "white") if str(g.get("color", "white")).isalnum() else "white"})
    out["graphics"] = gfx
    fit = tl.get("fit", "contain_blur")
    out["fit"] = fit if fit in FITS else "contain_blur"
    return out


def output_duration(tl: dict[str, Any]) -> float:
    return round(sum(c["out"] - c["in"] for c in tl["video"]["clips"]), 3)


def source_to_output(tl: dict[str, Any], a: float, b: float) -> list[tuple[float, float]]:
    """Proyecta un intervalo en tiempo de origen sobre el tiempo de salida."""
    result = []
    cursor = 0.0
    for c in tl["video"]["clips"]:
        lo, hi = max(a, c["in"]), min(b, c["out"])
        if hi > lo:
            result.append((round(cursor + lo - c["in"], 3), round(cursor + hi - c["in"], 3)))
        cursor += c["out"] - c["in"]
    return result


def source_point_to_output(tl: dict[str, Any], t: float) -> float | None:
    cursor = 0.0
    for c in tl["video"]["clips"]:
        if c["in"] <= t <= c["out"]:
            return round(cursor + t - c["in"], 3)
        cursor += c["out"] - c["in"]
    return None


def output_subtitles(tl: dict[str, Any]) -> list[dict[str, Any]]:
    items = []
    for it in tl["subtitles"]["items"]:
        if it.get("base") == "source":
            for s, e in source_to_output(tl, it["start"], it["end"]):
                if e - s >= 0.2:
                    items.append({"start": s, "end": e, "text": it["text"]})
        else:
            items.append({"start": it["start"], "end": it["end"], "text": it["text"]})
    return sorted(items, key=lambda x: x["start"])


def referenced_assets(tl: dict[str, Any]) -> set[str]:
    ids = {it["asset_id"] for it in tl.get("dialogue", {}).get("items", [])}
    for t in tl.get("tracks", []):
        ids |= {c["asset_id"] for c in t.get("clips", [])}
    return ids
