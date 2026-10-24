"""Interfaz común de los conectores de fuentes de vídeo.

Para añadir una fuente nueva basta con crear una subclase de ``Connector`` y
registrarla en ``registry.py`` (ver docs/CONECTORES.md). El editor y el resto
de la aplicación sólo conocen ``VideoCandidate``.
"""
from __future__ import annotations

import html
import re
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class LicenseInfo:
    name: str = "Desconocida"
    url: str = ""
    code: str = "unknown"
    allows_modification: bool | None = None
    allows_commercial: bool | None = None
    requires_attribution: bool | None = None
    share_alike: bool | None = None
    notes: list[str] = field(default_factory=list)


@dataclass
class VideoCandidate:
    connector: str
    id: str
    title: str
    page_url: str
    platform: str
    author: str = ""
    author_url: str = ""
    duration: float | None = None
    width: int | None = None
    height: int | None = None
    format: str = ""
    size_bytes: int | None = None
    thumbnail_url: str = ""
    embed_url: str = ""
    file_url: str = ""
    importable: bool = False
    reason: str = ""
    method: str = "B"  # A: URL directa · B: API oficial
    license: LicenseInfo = field(default_factory=LicenseInfo)
    attribution: str = ""
    alternatives: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d.pop("file_url", None)  # el enlace real del archivo no se expone al cliente
        return d


class Connector:
    name = "base"
    label = "Fuente"
    method = "B"
    searchable = False
    requires_key = False
    terms_url = ""
    docs_url = ""
    description = ""

    def status(self) -> dict[str, Any]:
        return {"available": True}

    def can_handle(self, url: str) -> bool:
        return False

    def search(self, query: str, page: int = 1, open_only: bool = True) -> list[VideoCandidate]:
        raise NotImplementedError

    def resolve(self, url: str) -> VideoCandidate:
        raise NotImplementedError

    def download_headers(self) -> dict[str, str]:
        return {}

    def info(self) -> dict[str, Any]:
        return {"name": self.name, "label": self.label, "method": self.method, "searchable": self.searchable,
                "requires_key": self.requires_key, "terms_url": self.terms_url, "docs_url": self.docs_url,
                "description": self.description, **self.status()}


_TAG = re.compile(r"<[^>]+>")


def strip_html(value: str | None) -> str:
    return html.unescape(_TAG.sub("", value or "")).strip()


def cc_license(name: str = "", url: str = "") -> LicenseInfo:
    """Interpreta licencias Creative Commons / dominio público a partir del nombre o la URL.

    Devuelve condiciones concretas; nunca asume que «licencia abierta» permite cualquier uso."""
    key = f"{name} {url}".lower()
    info = LicenseInfo(name=name or url or "Desconocida", url=url)
    if "publicdomain/zero" in key or re.search(r"\bcc0\b|cc-zero", key):
        info.code, info.name = "cc0", name or "CC0 1.0"
        info.allows_modification = info.allows_commercial = True
        info.requires_attribution = False
        info.share_alike = False
        return info
    if "public domain" in key or "publicdomain/mark" in key or re.search(r"\bpd\b|pd-", key) or "dominio público" in key:
        info.code = "public-domain"
        info.allows_modification = info.allows_commercial = True
        info.requires_attribution = False
        info.share_alike = False
        info.notes.append("Dominio público según la fuente: compruebe que lo es también en su país.")
        return info
    m = re.search(r"cc[\s-]?(by(?:[\s-](?:nc|nd|sa)){0,2})|licenses/(by(?:-(?:nc|nd|sa)){0,2})", key)
    if m:
        parts = (m.group(1) or m.group(2)).replace(" ", "-").split("-")
        info.code = "cc-" + "-".join(parts)
        info.requires_attribution = True
        info.allows_modification = "nd" not in parts
        info.allows_commercial = "nc" not in parts
        info.share_alike = "sa" in parts
        if "nd" in parts:
            info.notes.append("«Sin obras derivadas» (ND): no se permite modificar el vídeo.")
        if "nc" in parts:
            info.notes.append("«No comercial» (NC): el resultado no puede usarse con fines comerciales.")
        if "sa" in parts:
            info.notes.append("«Compartir igual» (SA): la obra derivada debe publicarse con la misma licencia.")
        info.notes.append("Exige atribución: autor, título, fuente y licencia.")
        return info
    if "gfdl" in key or "gnu free documentation" in key:
        info.code = "gfdl"
        info.allows_modification = True
        info.requires_attribution = True
        info.share_alike = True
        info.notes.append("GFDL: exige incluir el texto de la licencia; poco práctica para vídeo.")
        return info
    info.notes.append("Licencia no identificada automáticamente: compruebe sus condiciones.")
    return info


def attribution_text(title: str, author: str, platform: str, lic: LicenseInfo, url: str) -> str:
    who = author or "autor desconocido"
    return f"«{title}» de {who} ({platform}), {lic.name}. {url}".strip()
