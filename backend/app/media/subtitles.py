"""Subtítulos: troceado, temporización, SRT y ASS."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

MAX_LINE = 42
MAX_LINES = 2

_VOWELS = re.compile(r"[aeiouáéíóúüy]+", re.IGNORECASE)


def syllables(text: str) -> int:
    """Aproximación de sílabas en español por grupos vocálicos (sin hiatos)."""
    count = 0
    for word in re.findall(r"[\wáéíóúüñ]+", text.lower()):
        if word.isdigit():
            count += max(1, len(word)) * 2
            continue
        groups = _VOWELS.findall(word)
        n = len(groups)
        # hiatos frecuentes con vocales fuertes (a, e, o) contiguas
        for g in groups:
            n += len(re.findall(r"(?<=[aeoáéó])[aeoáéó]", g))
        count += max(1, n)
    return count


def wrap(text: str, width: int = MAX_LINE) -> list[str]:
    words = text.split()
    out, cur = [], ""
    for w in words:
        if cur and len(cur) + 1 + len(w) > width:
            out.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    if cur:
        out.append(cur)
    return out


def chunk_text(text: str) -> list[str]:
    """Divide en bloques de hasta 2 líneas, cortando preferentemente en puntuación."""
    sentences = [s.strip() for s in re.split(r"(?<=[.!?¿¡;:])\s+", text.strip()) if s.strip()]
    blocks: list[str] = []
    for s in sentences:
        lines = wrap(s)
        for i in range(0, len(lines), MAX_LINES):
            blocks.append("\n".join(lines[i:i + MAX_LINES]))
    return blocks


def time_blocks(blocks: list[str], start: float, end: float) -> list[dict[str, Any]]:
    """Reparte el intervalo proporcionalmente a las sílabas de cada bloque."""
    weights = [max(1, syllables(b)) for b in blocks]
    total = sum(weights) or 1
    span = max(0.0, end - start)
    out, t = [], start
    for b, w in zip(blocks, weights):
        d = span * w / total
        out.append({"start": round(t, 3), "end": round(t + d, 3), "text": b})
        t += d
    return out


def _ts_srt(t: float) -> str:
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def to_srt(items: list[dict[str, Any]]) -> str:
    lines = []
    for i, it in enumerate(sorted(items, key=lambda x: x["start"]), 1):
        lines += [str(i), f"{_ts_srt(it['start'])} --> {_ts_srt(it['end'])}", it["text"], ""]
    return "\n".join(lines)


def _ts_ass(t: float) -> str:
    cs = int(round(t * 100))
    h, cs = divmod(cs, 360_000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h:d}:{m:02d}:{s:02d}.{cs:02d}"


def _ass_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}").replace("\n", "\\N")


def write_ass(items: list[dict[str, Any]], path: Path, width: int, height: int,
              style: dict[str, Any] | None = None) -> Path:
    style = style or {}
    size = int(height * float(style.get("size", 0.05)))
    margin_v = int(height * float(style.get("margin", 0.12)))
    primary = style.get("color", "&H00FFFFFF")
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,DejaVu Sans,{size},{primary},&H000000FF,&H00000000,&H80000000,1,0,0,0,100,100,0,0,1,{max(1, size // 14)},0,2,{int(width * 0.06)},{int(width * 0.06)},{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    events = [
        f"Dialogue: 0,{_ts_ass(it['start'])},{_ts_ass(it['end'])},Default,,0,0,0,,{_ass_escape(it['text'])}"
        for it in sorted(items, key=lambda x: x["start"]) if it["end"] > it["start"]
    ]
    path.write_text(header + "\n".join(events) + "\n", encoding="utf-8")
    return path
