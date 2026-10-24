"""Modelo de la línea de tiempo multipista con varias fuentes.

Dos bases temporales:

* **Tiempo de origen** (por fuente): segundos dentro del vídeo de una fuente.
  Diálogos sincronizados, silencios del audio original y subtítulos de
  diálogo están ligados a la imagen y se expresan así (``source_id`` +
  tiempo), de modo que siguen a la escena aunque se recorte, reordene o
  cambie de velocidad.
* **Tiempo de salida**: segundos del vídeo final. Música, audio adicional,
  subtítulos libres y gráficos usan esta base.

Estructura::

    {
      "video": {"clips": [{"id","source_id","in","out","speed","volume",
                           "fade_in","fade_out","color":{"brightness","contrast","saturation"}}]},
      "source_audio": {"volume": 1.0, "mutes": [{"source_id","start","end"}]},
      "dialogue": {"volume": 1.0, "items": [{"id","dialogue_id","asset_id","source_id","source_start","volume"}]},
      "tracks": [{"id","name","kind":"audio|music","volume","muted",
                  "clips":[{"id","asset_id","start","in","out","volume"}]}],
      "subtitles": {"enabled": true, "style": {...},
                    "items": [{"id","start","end","text","base":"source|output","source_id"?}]},
      "graphics": [{"id","start","end","text","x","y","size","color"}],
      "credits": {"enabled": true, "seconds": 4},
      "fit": "contain_blur|contain|cover"
    }
"""
from __future__ import annotations

import uuid
from typing import Any

from ..errors import AppError

FITS = {"contain_blur", "contain", "cover"}
COLORS = {"white", "yellow", "black", "red", "cyan"}


def _id() -> str:
    return uuid.uuid4().hex[:10]


def empty() -> dict[str, Any]:
    return {
        "video": {"clips": []},
        "source_audio": {"volume": 1.0, "mutes": []},
        "dialogue": {"volume": 1.0, "items": []},
        "tracks": [
            {"id": "voz_extra", "name": "Audio", "kind": "audio", "volume": 1.0, "muted": False, "clips": []},
            {"id": "musica", "name": "Música", "kind": "music", "volume": 0.6, "muted": False, "clips": []},
        ],
        "subtitles": {"enabled": True, "style": {"size": 0.05, "margin": 0.14}, "items": []},
        "graphics": [],
        "credits": {"enabled": True, "seconds": 4.0},
        "fit": "contain_blur",
    }


def new_clip(source_id: str, a: float, b: float) -> dict[str, Any]:
    return {"id": _id(), "source_id": source_id, "in": round(a, 3), "out": round(b, 3), "speed": 1.0,
            "volume": 1.0, "fade_in": 0.0, "fade_out": 0.0,
            "color": {"brightness": 0.0, "contrast": 1.0, "saturation": 1.0}}


def _num(v: Any, name: str, lo: float | None = None, hi: float | None = None) -> float:
    try:
        f = float(v)
    except (TypeError, ValueError):
        raise AppError(f"Valor numérico no válido en «{name}».", code="linea_de_tiempo")
    if f != f:  # NaN
        raise AppError(f"Valor numérico no válido en «{name}».", code="linea_de_tiempo")
    if lo is not None and f < lo:
        raise AppError(f"«{name}» no puede ser menor que {lo}.", code="linea_de_tiempo")
    if hi is not None and f > hi:
        raise AppError(f"«{name}» no puede ser mayor que {hi}.", code="linea_de_tiempo")
    return round(f, 3)


def _src(v: Any, durations: dict[str, float], what: str) -> str:
    sid = str(v or "")
    if sid not in durations:
        raise AppError(f"{what} hace referencia a una fuente de vídeo inexistente.", code="linea_de_tiempo")
    return sid


def validate(tl: dict[str, Any], durations: dict[str, float], known_assets: set[str]) -> dict[str, Any]:
    """Valida y normaliza una línea de tiempo recibida del cliente.

    ``durations``: duración de cada fuente lista (id → segundos)."""
    base = empty()
    out: dict[str, Any] = {}
    clips = []
    for c in (tl.get("video") or {}).get("clips", []):
        sid = _src(c.get("source_id"), durations, "Una escena")
        dur = durations[sid]
        a = _num(c.get("in"), "inicio de escena", 0, dur + 0.01)
        b = min(_num(c.get("out"), "fin de escena", 0, dur + 0.01), round(dur, 3))
        if b - a < 0.1:
            raise AppError("Cada escena debe durar al menos 0,1 s.", code="linea_de_tiempo")
        col = c.get("color") or {}
        speed = _num(c.get("speed", 1.0), "velocidad", 0.5, 2.0)
        length = (b - a) / speed
        fi = _num(c.get("fade_in", 0), "fundido de entrada", 0, 5)
        fo = _num(c.get("fade_out", 0), "fundido de salida", 0, 5)
        if fi + fo > length:
            raise AppError("Los fundidos no pueden durar más que la escena.", code="linea_de_tiempo")
        clips.append({"id": str(c.get("id") or _id()), "source_id": sid, "in": a, "out": b, "speed": speed,
                      "volume": _num(c.get("volume", 1.0), "volumen de la escena", 0, 2),
                      "fade_in": fi, "fade_out": fo,
                      "color": {"brightness": _num(col.get("brightness", 0), "brillo", -0.5, 0.5),
                                "contrast": _num(col.get("contrast", 1), "contraste", 0.5, 2.0),
                                "saturation": _num(col.get("saturation", 1), "saturación", 0, 3)}})
    out["video"] = {"clips": clips}

    sa = tl.get("source_audio") or {}
    mutes = []
    for m in sa.get("mutes", []):
        sid = _src(m.get("source_id"), durations, "Un silencio")
        s, e = _num(m.get("start"), "silencio", 0), _num(m.get("end"), "silencio", 0)
        if e > s:
            mutes.append({"source_id": sid, "start": s, "end": e})
    out["source_audio"] = {"volume": _num(sa.get("volume", 1.0), "volumen original", 0, 2), "mutes": mutes}

    dl = tl.get("dialogue") or {}
    items = []
    for it in dl.get("items", []):
        if it.get("asset_id") not in known_assets:
            raise AppError("Un diálogo hace referencia a un audio inexistente.", code="linea_de_tiempo")
        sid = _src(it.get("source_id"), durations, "Un diálogo")
        items.append({"id": str(it.get("id") or _id()), "dialogue_id": it.get("dialogue_id"), "asset_id": it["asset_id"],
                      "source_id": sid,
                      "source_start": _num(it.get("source_start", 0), "inicio del diálogo", 0, durations[sid]),
                      "volume": _num(it.get("volume", 1.0), "volumen del diálogo", 0, 2)})
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
        if item["base"] == "source":
            item["source_id"] = _src(it.get("source_id"), durations, "Un subtítulo")
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
                    "color": g.get("color") if g.get("color") in COLORS else "white"})
    out["graphics"] = gfx
    cr = tl.get("credits") or {}
    out["credits"] = {"enabled": bool(cr.get("enabled", True)), "seconds": _num(cr.get("seconds", 4), "créditos", 2, 15)}
    fit = tl.get("fit", "contain_blur")
    out["fit"] = fit if fit in FITS else "contain_blur"
    return out


def clip_length(c: dict[str, Any]) -> float:
    return (c["out"] - c["in"]) / c.get("speed", 1.0)


def output_duration(tl: dict[str, Any]) -> float:
    return round(sum(clip_length(c) for c in tl["video"]["clips"]), 3)


def source_to_output(tl: dict[str, Any], source_id: str, a: float, b: float) -> list[tuple[float, float]]:
    """Proyecta un intervalo [a, b] de una fuente sobre el tiempo de salida."""
    result = []
    cursor = 0.0
    for c in tl["video"]["clips"]:
        sp = c.get("speed", 1.0)
        if c["source_id"] == source_id:
            lo, hi = max(a, c["in"]), min(b, c["out"])
            if hi > lo:
                result.append((round(cursor + (lo - c["in"]) / sp, 3), round(cursor + (hi - c["in"]) / sp, 3)))
        cursor += clip_length(c)
    return result


def output_subtitles(tl: dict[str, Any]) -> list[dict[str, Any]]:
    items = []
    for it in tl["subtitles"]["items"]:
        if it.get("base") == "source":
            for s, e in source_to_output(tl, it["source_id"], it["start"], it["end"]):
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


def used_sources(tl: dict[str, Any]) -> list[str]:
    seen: list[str] = []
    for c in tl["video"]["clips"]:
        if c["source_id"] not in seen:
            seen.append(c["source_id"])
    return seen


def remove_source(tl: dict[str, Any], source_id: str) -> dict[str, Any]:
    tl["video"]["clips"] = [c for c in tl["video"]["clips"] if c["source_id"] != source_id]
    tl["source_audio"]["mutes"] = [m for m in tl["source_audio"]["mutes"] if m["source_id"] != source_id]
    tl["dialogue"]["items"] = [i for i in tl["dialogue"]["items"] if i["source_id"] != source_id]
    tl["subtitles"]["items"] = [s for s in tl["subtitles"]["items"] if s.get("source_id") != source_id]
    return tl
