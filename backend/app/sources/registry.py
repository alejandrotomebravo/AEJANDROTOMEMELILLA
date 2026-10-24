"""Registro de conectores. Para añadir una fuente: créela en connectors.py (o en
un módulo propio), añádala a ``ALL`` y a ENABLED_CONNECTORS."""
from __future__ import annotations

from ..config import settings
from ..errors import AppError, ProviderUnavailable
from .base import Connector, VideoCandidate
from .connectors import DirectURL, InternetArchive, Pexels, Vimeo, WikimediaCommons, YouTube

ALL: dict[str, type[Connector]] = {c.name: c for c in (WikimediaCommons, InternetArchive, Pexels, Vimeo, YouTube, DirectURL)}


def enabled() -> list[Connector]:
    names = [n.strip() for n in settings.enabled_connectors.split(",") if n.strip()]
    return [ALL[n]() for n in names if n in ALL]


def get(name: str) -> Connector:
    for c in enabled():
        if c.name == name:
            return c
    raise ProviderUnavailable(f"La fuente «{name}» no está habilitada.")


def for_url(url: str) -> Connector:
    """Elige el conector específico de la plataforma; si no hay, el de enlace directo."""
    conns = enabled()
    for c in conns:
        if c.name != "direct_url" and c.can_handle(url):
            return c
    for c in conns:
        if c.name == "direct_url":
            return c
    raise AppError("No hay ningún conector habilitado para esta dirección.", code="sin_conector")


def resolve(url: str) -> VideoCandidate:
    conn = for_url(url)
    st = conn.status()
    if not st.get("available", True):
        raise ProviderUnavailable(st.get("reason") or "Fuente no disponible.")
    return conn.resolve(url)
