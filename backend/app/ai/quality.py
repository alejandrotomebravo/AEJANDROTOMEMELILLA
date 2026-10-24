"""Evaluación previa de la calidad del material para sincronización labial y
sustitución facial. El objetivo es avisar ANTES de gastar procesamiento (y
dinero) en lugar de producir silenciosamente un resultado defectuoso.

Los umbrales son heurísticos y conservadores; están documentados en
docs/LIMITACIONES.md.
"""
from __future__ import annotations

from typing import Any

import numpy as np

from .face_detect import iou

MIN_FACE_PX_BLOCK = 64
MIN_FACE_PX_WARN = 128
MIN_COVERAGE_BLOCK = 0.6
MIN_COVERAGE_WARN = 0.9
MAX_YAW_WARN = 0.35
MAX_BRIGHTNESS_STD_WARN = 25.0
MAX_MOTION_WARN = 0.35  # desplazamiento medio por muestra relativo al tamaño de la cara


def assess_segment(track: list[dict], others: list[list[dict]], start: float, end: float,
                   interval: float) -> dict[str, Any]:
    seg = [d for d in track if start - 1e-6 <= d["t"] <= end + 1e-6]
    # Instantes de muestreo del análisis (múltiplos de ``interval``) que caen dentro del fragmento.
    expected = max(1, int(np.floor(end / interval + 1e-6)) - int(np.ceil(start / interval - 1e-6)) + 1)
    coverage = min(1.0, len(seg) / expected)
    blocking: list[str] = []
    warnings: list[str] = []
    metrics: dict[str, Any] = {"coverage": round(coverage, 2), "samples": len(seg), "expected": expected}

    if not seg:
        blocking.append("El rostro seleccionado no aparece en este fragmento.")
        return {"ok": False, "blocking": blocking, "warnings": warnings, "metrics": metrics}

    heights = np.array([d["h"] for d in seg])
    med_h = float(np.median(heights))
    metrics["median_face_px"] = int(med_h)
    if coverage < MIN_COVERAGE_BLOCK:
        blocking.append(f"El rostro sólo es visible en el {coverage:.0%} del fragmento; "
                        "la boca no podrá seguirse de forma fiable.")
    elif coverage < MIN_COVERAGE_WARN:
        warnings.append(f"El rostro se pierde en parte del fragmento (visible un {coverage:.0%}).")
    if med_h < MIN_FACE_PX_BLOCK:
        blocking.append(f"El rostro es demasiado pequeño ({int(med_h)} px). Se necesitan al menos "
                        f"{MIN_FACE_PX_BLOCK} px; use un plano más cercano o mayor resolución.")
    elif med_h < MIN_FACE_PX_WARN:
        warnings.append(f"Rostro pequeño ({int(med_h)} px): el resultado puede verse borroso.")

    yaws = [abs(d["yaw"]) for d in seg if d.get("yaw") is not None]
    profiles = sum(1 for d in seg if d.get("profile"))
    if yaws:
        metrics["max_yaw"] = round(max(yaws), 2)
        if np.mean(np.array(yaws) > MAX_YAW_WARN) > 0.3:
            warnings.append("La cabeza aparece muy girada durante buena parte del fragmento.")
    if profiles / len(seg) > 0.3:
        warnings.append("El rostro aparece de perfil con frecuencia; la sincronización será menos precisa.")

    bright = [d["brightness"] for d in seg if d.get("brightness") is not None]
    if len(bright) > 2:
        std = float(np.std(bright))
        metrics["brightness_std"] = round(std, 1)
        if std > MAX_BRIGHTNESS_STD_WARN:
            warnings.append("Hay cambios fuertes de iluminación sobre el rostro.")
        if np.mean(bright) < 40:
            warnings.append("El rostro está muy oscuro.")

    if len(seg) > 2:
        moves = []
        for a, b in zip(seg, seg[1:]):
            dx = (b["x"] + b["w"] / 2) - (a["x"] + a["w"] / 2)
            dy = (b["y"] + b["h"] / 2) - (a["y"] + a["h"] / 2)
            moves.append(np.hypot(dx, dy) / max(1.0, (a["h"] + b["h"]) / 2))
        motion = float(np.mean(moves))
        metrics["motion"] = round(motion, 2)
        if motion > MAX_MOTION_WARN:
            warnings.append("La cabeza se mueve mucho; pueden aparecer saltos o temblores.")

    overlaps = 0
    for o in others:
        for d in seg:
            if any(abs(od["t"] - d["t"]) < 1e-3 and iou(od, d) > 0.2 for od in o):
                overlaps += 1
    if overlaps:
        warnings.append("Otro rostro se superpone con el seleccionado en algunos momentos.")

    return {"ok": not blocking, "blocking": blocking, "warnings": warnings, "metrics": metrics}


def assess_face_photo(width: int, height: int, faces: int, face_px: int) -> dict[str, Any]:
    blocking, warnings = [], []
    if faces == 0:
        blocking.append("No se detecta ningún rostro en la fotografía.")
    elif faces > 1:
        blocking.append("La fotografía contiene varios rostros; use una en la que sólo aparezca la persona.")
    elif face_px < 96:
        blocking.append("El rostro de la fotografía es demasiado pequeño (mínimo 96 px).")
    elif face_px < 200:
        warnings.append("El rostro de la fotografía es pequeño; una imagen más cercana mejorará el resultado.")
    return {"ok": not blocking, "blocking": blocking, "warnings": warnings}
