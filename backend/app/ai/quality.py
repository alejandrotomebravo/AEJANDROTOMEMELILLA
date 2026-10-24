"""Control de calidad de las fotografías de referencia de los personajes: avisa ANTES de gastar
procesamiento (y dinero) si la foto no sirve, en lugar de producir un resultado defectuoso."""
from __future__ import annotations

from typing import Any


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
