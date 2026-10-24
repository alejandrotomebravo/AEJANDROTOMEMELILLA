"""Conectores de fuentes de vídeo.

Estado de verificación de condiciones (octubre de 2026):

* **Wikimedia Commons** — API MediaWiki pública; los archivos se publican con
  licencias libres o en dominio público, y la API devuelve la licencia y el
  autor (extmetadata). Exige un User-Agent identificable. Importable.
* **Internet Archive** — API de búsqueda y de metadatos públicas, descarga
  directa de archivos. La licencia no siempre se indica (campo ``licenseurl``):
  si falta, el usuario debe acreditar sus derechos. Los elementos de préstamo
  restringido no se importan.
* **Pexels** — API oficial con clave; licencia Pexels (uso y modificación
  gratuitos, con restricciones: no mostrar a personas identificables de forma
  ofensiva, no sugerir respaldo, no revender copias sin modificar). Importable.
* **Vimeo** — API oficial; sólo devuelve enlaces de descarga si el token tiene
  el permiso ``video_files`` y el plan/propietario lo permite (normalmente,
  vídeos propios). Importable sólo en ese caso.
* **YouTube** — sus condiciones prohíben descargar contenido salvo mediante
  las funciones del propio servicio, y la API de datos no ofrece descarga.
  Sólo búsqueda y metadatos; NO importable.
* **URL directa** — enlace a un archivo de vídeo (método A) con descarga segura.
"""
from __future__ import annotations

import re
from typing import Any
from urllib.parse import quote, unquote, urlencode, urlsplit

from ..config import settings
from ..errors import AppError, ProviderUnavailable
from .. import net
from .base import Connector, LicenseInfo, VideoCandidate, attribution_text, cc_license, strip_html

VIDEO_EXT = (".mp4", ".m4v", ".mov", ".webm", ".ogv", ".ogg", ".mkv", ".avi", ".mpeg", ".mpg", ".ts")


def _iso8601_duration(v: str | None) -> float | None:
    if not v:
        return None
    m = re.fullmatch(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?", v)
    if not m:
        return None
    d, h, mi, s = (float(x) if x else 0.0 for x in m.groups())
    return d * 86400 + h * 3600 + mi * 60 + s


# ---------------------------------------------------------------- Wikimedia Commons

class WikimediaCommons(Connector):
    name = "wikimedia"
    label = "Wikimedia Commons"
    searchable = True
    terms_url = "https://commons.wikimedia.org/wiki/Commons:Reusing_content_outside_Wikimedia"
    docs_url = "https://www.mediawiki.org/wiki/API:Imageinfo"
    description = "Vídeos con licencias libres o de dominio público, con autor y licencia identificados."
    API = "https://commons.wikimedia.org/w/api.php"
    EXT_FIELDS = "LicenseShortName|LicenseUrl|Artist|Credit|AttributionRequired|UsageTerms|ImageDescription|Restrictions|Copyrighted"

    def can_handle(self, url: str) -> bool:
        host = urlsplit(url).hostname or ""
        return host.endswith("commons.wikimedia.org") or (host == "upload.wikimedia.org" and "/commons/" in url)

    def _query(self, **params: Any) -> dict[str, Any]:
        base = {"action": "query", "format": "json", "formatversion": "2", "prop": "imageinfo",
                "iiprop": "url|size|mime|mediatype|extmetadata", "iiextmetadatafilter": self.EXT_FIELDS,
                "iiurlwidth": "320"}
        base.update(params)
        return net.get_json(f"{self.API}?{urlencode(base)}")

    def search(self, query: str, page: int = 1, open_only: bool = True) -> list[VideoCandidate]:
        data = self._query(generator="search", gsrsearch=f"filetype:video {query}", gsrnamespace="6",
                           gsrlimit="20", gsroffset=str((page - 1) * 20))
        pages = (data.get("query") or {}).get("pages") or []
        return [c for c in (self._candidate(p) for p in sorted(pages, key=lambda x: x.get("index", 0))) if c]

    def resolve(self, url: str) -> VideoCandidate:
        path = unquote(urlsplit(url).path)
        m = re.search(r"/wiki/(File:[^/?#]+)", path) or re.search(r"/([^/]+\.(?:webm|ogv|ogg|mp4|mpeg|mpg))$", path, re.I)
        if not m:
            raise AppError("No se reconoce la dirección como un archivo de Wikimedia Commons.", code="url_no_reconocida")
        title = m.group(1) if m.group(1).startswith("File:") else f"File:{m.group(1)}"
        data = self._query(titles=title.replace("_", " "))
        pages = (data.get("query") or {}).get("pages") or []
        cand = self._candidate(pages[0]) if pages else None
        if not cand:
            raise AppError("El archivo no existe en Wikimedia Commons o no es un vídeo.", code="no_encontrado", status=404)
        return cand

    def _candidate(self, page: dict[str, Any]) -> VideoCandidate | None:
        ii = (page.get("imageinfo") or [None])[0]
        if not ii or ii.get("mediatype") != "VIDEO":
            return None
        meta = {k: (v or {}).get("value") for k, v in (ii.get("extmetadata") or {}).items()}
        lic = cc_license(strip_html(meta.get("LicenseShortName")), meta.get("LicenseUrl") or "")
        if str(meta.get("AttributionRequired", "")).lower() == "true":
            lic.requires_attribution = True
        if meta.get("Restrictions"):
            lic.notes.append(f"Restricciones adicionales indicadas en Commons: {strip_html(meta['Restrictions'])} "
                             "(p. ej., derechos de la personalidad de las personas que aparecen).")
        title = page.get("title", "").removeprefix("File:")
        author = strip_html(meta.get("Artist"))[:200]
        cand = VideoCandidate(
            connector=self.name, id=page.get("title", ""), title=title, page_url=ii.get("descriptionurl", ""),
            platform=self.label, author=author, width=ii.get("width"), height=ii.get("height"),
            format=ii.get("mime", ""), size_bytes=ii.get("size"), thumbnail_url=ii.get("thumburl", ""),
            file_url=ii.get("url", ""), importable=bool(ii.get("url")), method="B", license=lic,
            duration=ii.get("duration"),
        )
        if lic.allows_modification is False:
            cand.importable = False
            cand.reason = "La licencia no permite obras derivadas (ND)."
        cand.attribution = attribution_text(title, author, self.label, lic, cand.page_url)
        return cand


# ---------------------------------------------------------------- Internet Archive

class InternetArchive(Connector):
    name = "internet_archive"
    label = "Internet Archive"
    searchable = True
    terms_url = "https://archive.org/about/terms.php"
    docs_url = "https://archive.org/developers/"
    description = "Archivo de películas y vídeos; muchos en dominio público. La licencia no siempre consta."
    PREFERRED = ("h.264", "mpeg4", "h.264 ia", "512kb mpeg4", "webm", "ogg video", "matroska", "quicktime")

    def can_handle(self, url: str) -> bool:
        return (urlsplit(url).hostname or "").endswith("archive.org")

    def search(self, query: str, page: int = 1, open_only: bool = True) -> list[VideoCandidate]:
        q = f"({query}) AND mediatype:(movies)"
        if open_only:
            q += " AND (licenseurl:*creativecommons.org* OR licenseurl:*publicdomain*)"
        params = [("q", q), ("rows", "20"), ("page", str(page)), ("output", "json")]
        params += [("fl[]", f) for f in ("identifier", "title", "creator", "licenseurl", "year", "runtime")]
        data = net.get_json(f"https://archive.org/advancedsearch.php?{urlencode(params)}")
        out = []
        for d in (data.get("response") or {}).get("docs", []):
            ident = d.get("identifier")
            if not ident:
                continue
            lic = cc_license("", d.get("licenseurl") or "") if d.get("licenseurl") else LicenseInfo(
                notes=["El elemento no declara licencia: acredite sus derechos antes de usarlo."])
            title = d.get("title") if isinstance(d.get("title"), str) else ident
            creator = d.get("creator")
            creator = ", ".join(creator) if isinstance(creator, list) else (creator or "")
            out.append(VideoCandidate(
                connector=self.name, id=ident, title=title, page_url=f"https://archive.org/details/{ident}",
                platform=self.label, author=creator, thumbnail_url=f"https://archive.org/services/img/{quote(ident)}",
                importable=True, reason="Se elegirá el archivo de vídeo al importar.", license=lic, method="B"))
        return out

    def resolve(self, url: str) -> VideoCandidate:
        m = re.search(r"archive\.org/(?:details|download|embed)/([^/?#]+)", url)
        ident = m.group(1) if m else (url if re.fullmatch(r"[\w.\-]+", url) else None)
        if not ident:
            raise AppError("No se reconoce la dirección como un elemento de Internet Archive.", code="url_no_reconocida")
        data = net.get_json(f"https://archive.org/metadata/{quote(ident)}")
        if not data or "metadata" not in data:
            raise AppError("El elemento no existe en Internet Archive.", code="no_encontrado", status=404)
        md = data["metadata"]
        title = md.get("title") if isinstance(md.get("title"), str) else ident
        creator = md.get("creator")
        creator = ", ".join(creator) if isinstance(creator, list) else (creator or "")
        lic = cc_license("", md.get("licenseurl") or "") if md.get("licenseurl") else LicenseInfo(
            notes=["El elemento no declara licencia. Muchos contenidos los suben usuarios: compruebe la titularidad."])
        cand = VideoCandidate(connector=self.name, id=ident, title=title, page_url=f"https://archive.org/details/{ident}",
                              platform=self.label, author=creator, license=lic, method="B",
                              thumbnail_url=f"https://archive.org/services/img/{quote(ident)}",
                              embed_url=f"https://archive.org/embed/{quote(ident)}")
        if md.get("access-restricted-item") in (True, "true"):
            cand.reason = "Elemento de préstamo restringido: la fuente no permite descargarlo."
            cand.alternatives = ["Busque otra copia con licencia abierta o solicite permiso al titular."]
            return cand
        best = self._best_file(data.get("files") or [])
        if not best:
            cand.reason = "El elemento no contiene archivos de vídeo descargables."
            return cand
        cand.file_url = f"https://archive.org/download/{quote(ident)}/{quote(best['name'])}"
        cand.format = best.get("format", "")
        cand.size_bytes = int(best["size"]) if str(best.get("size", "")).isdigit() else None
        cand.width = int(best["width"]) if str(best.get("width", "")).isdigit() else None
        cand.height = int(best["height"]) if str(best.get("height", "")).isdigit() else None
        try:
            cand.duration = float(best.get("length")) if best.get("length") else None
        except ValueError:
            cand.duration = None
        cand.importable = lic.allows_modification is not False
        if not cand.importable:
            cand.reason = "La licencia no permite obras derivadas (ND)."
        cand.attribution = attribution_text(title, creator, self.label, lic, cand.page_url)
        return cand

    def _best_file(self, files: list[dict[str, Any]]) -> dict[str, Any] | None:
        vids = [f for f in files if str(f.get("name", "")).lower().endswith(VIDEO_EXT)
                and f.get("source") in ("original", "derivative", None)]
        if not vids:
            return None

        def score(f: dict[str, Any]) -> tuple:
            fmt = str(f.get("format", "")).lower()
            pref = next((i for i, p in enumerate(self.PREFERRED) if p == fmt), len(self.PREFERRED))
            h = int(f["height"]) if str(f.get("height", "")).isdigit() else 0
            return (pref, -min(h, 1080))
        return sorted(vids, key=score)[0]


# ---------------------------------------------------------------- Pexels

class Pexels(Connector):
    name = "pexels"
    label = "Pexels"
    searchable = True
    requires_key = True
    terms_url = "https://www.pexels.com/license/"
    docs_url = "https://www.pexels.com/api/documentation/"
    description = "Vídeos de stock con la licencia de Pexels (uso y modificación gratuitos, con restricciones)."

    def status(self) -> dict[str, Any]:
        if not settings.pexels_api_key:
            return {"available": False, "reason": "Requiere una clave gratuita de la API de Pexels (PEXELS_API_KEY)."}
        return {"available": True}

    def can_handle(self, url: str) -> bool:
        return (urlsplit(url).hostname or "").endswith("pexels.com")

    def _headers(self) -> dict[str, str]:
        if not settings.pexels_api_key:
            raise ProviderUnavailable(self.status()["reason"])
        return {"Authorization": settings.pexels_api_key}

    def search(self, query: str, page: int = 1, open_only: bool = True) -> list[VideoCandidate]:
        data = net.get_json(f"https://api.pexels.com/videos/search?{urlencode({'query': query, 'per_page': 15, 'page': page})}",
                            headers=self._headers())
        return [self._candidate(v) for v in data.get("videos", [])]

    def resolve(self, url: str) -> VideoCandidate:
        m = re.search(r"/video/(?:[^/]*-)?(\d+)/?", url) or re.fullmatch(r"(\d+)", url)
        if not m:
            raise AppError("No se reconoce la dirección como un vídeo de Pexels.", code="url_no_reconocida")
        return self._candidate(net.get_json(f"https://api.pexels.com/videos/videos/{m.group(1)}", headers=self._headers()))

    def _candidate(self, v: dict[str, Any]) -> VideoCandidate:
        files = [f for f in v.get("video_files", []) if f.get("file_type") == "video/mp4" and f.get("link")]
        files.sort(key=lambda f: (abs((f.get("height") or 0) - 1080), -(f.get("width") or 0)))
        best = files[0] if files else None
        user = v.get("user") or {}
        lic = LicenseInfo(name="Licencia de Pexels", url="https://www.pexels.com/license/", code="pexels",
                          allows_modification=True, allows_commercial=True, requires_attribution=False,
                          share_alike=False,
                          notes=["No muestre a personas identificables de forma ofensiva ni sugiera que respaldan algo.",
                                 "La licencia de Pexels no equivale al consentimiento de las personas que aparecen "
                                 "para alterar su rostro o su voz.",
                                 "Atribución no obligatoria, pero recomendada (autor y Pexels)."])
        cand = VideoCandidate(connector=self.name, id=str(v.get("id")), title=f"Vídeo de Pexels {v.get('id')}",
                              page_url=v.get("url", ""), platform=self.label, author=user.get("name", ""),
                              author_url=user.get("url", ""), duration=v.get("duration"),
                              width=(best or {}).get("width") or v.get("width"), height=(best or {}).get("height") or v.get("height"),
                              format="video/mp4", thumbnail_url=v.get("image", ""), file_url=(best or {}).get("link", ""),
                              importable=bool(best), reason="" if best else "Sin archivo MP4 disponible.", license=lic)
        cand.attribution = f"Vídeo de {cand.author or 'autor'} en Pexels. {cand.page_url}"
        return cand


# ---------------------------------------------------------------- Vimeo

class Vimeo(Connector):
    name = "vimeo"
    label = "Vimeo"
    requires_key = True
    terms_url = "https://vimeo.com/terms"
    docs_url = "https://developer.vimeo.com/api/reference/videos"
    description = "Sólo vídeos cuyo propietario permite la descarga a través de la API (normalmente, los suyos)."

    def status(self) -> dict[str, Any]:
        if not settings.vimeo_access_token:
            return {"available": False, "reason": "Requiere un token de la API de Vimeo con permiso «video_files» "
                                                  "(VIMEO_ACCESS_TOKEN). No se usan contraseñas."}
        return {"available": True}

    def can_handle(self, url: str) -> bool:
        return (urlsplit(url).hostname or "").endswith("vimeo.com")

    def resolve(self, url: str) -> VideoCandidate:
        m = re.search(r"vimeo\.com/(?:video/|channels/[^/]+/|groups/[^/]+/videos/)?(\d+)", url)
        if not m:
            raise AppError("No se reconoce la dirección como un vídeo de Vimeo.", code="url_no_reconocida")
        vid = m.group(1)
        cand = VideoCandidate(connector=self.name, id=vid, title=f"Vídeo de Vimeo {vid}", page_url=f"https://vimeo.com/{vid}",
                              platform=self.label, embed_url=f"https://player.vimeo.com/video/{vid}", method="B")
        if not settings.vimeo_access_token:
            cand.reason = self.status()["reason"]
            cand.alternatives = ["Si el vídeo es suyo, descárguelo desde su cuenta de Vimeo e impórtelo como archivo."]
            return cand
        v = net.get_json(f"https://api.vimeo.com/videos/{vid}?fields=name,user.name,user.link,duration,width,height,license,download,pictures.base_link,link",
                         headers=self.download_headers() | {"Accept": "application/vnd.vimeo.*+json;version=3.4"})
        cand.title = v.get("name") or cand.title
        cand.author = (v.get("user") or {}).get("name", "")
        cand.author_url = (v.get("user") or {}).get("link", "")
        cand.duration, cand.width, cand.height = v.get("duration"), v.get("width"), v.get("height")
        cand.thumbnail_url = (v.get("pictures") or {}).get("base_link", "")
        lic_code = v.get("license")
        cand.license = cc_license(f"CC {lic_code}", "") if lic_code else LicenseInfo(
            name="Todos los derechos reservados (salvo autorización)", code="all-rights-reserved",
            allows_modification=False, notes=["Sin licencia abierta: necesita autorización del titular."])
        downloads = [d for d in (v.get("download") or []) if d.get("link")]
        if not downloads:
            cand.reason = ("Vimeo no ofrece descarga de este vídeo para su token (sólo el propietario, con un plan que "
                           "lo permita y el permiso «video_files»).")
            cand.alternatives = ["Pida al titular el archivo original o su autorización de descarga."]
            return cand
        downloads.sort(key=lambda d: (abs((d.get("height") or 0) - 1080), -(d.get("size") or 0)))
        best = downloads[0]
        cand.file_url, cand.size_bytes, cand.format = best["link"], best.get("size"), best.get("type", "video/mp4")
        cand.importable = True
        cand.attribution = attribution_text(cand.title, cand.author, self.label, cand.license, cand.page_url)
        return cand

    def download_headers(self) -> dict[str, str]:
        return {"Authorization": f"bearer {settings.vimeo_access_token}"} if settings.vimeo_access_token else {}


# ---------------------------------------------------------------- YouTube (sólo metadatos)

YT_REASON = ("Las condiciones de servicio de YouTube no permiten descargar vídeos salvo con las funciones del propio "
             "servicio, y su API oficial no ofrece descarga. La aplicación no elude esta restricción.")
YT_ALTERNATIVES = [
    "Si el vídeo es suyo, descárguelo desde YouTube Studio (Contenido → Opciones → Descargar) e impórtelo como archivo.",
    "Si es de otra persona, pida al autor el archivo original y una autorización por escrito.",
    "Busque material equivalente con licencia abierta en Wikimedia Commons, Internet Archive o Pexels.",
]


class YouTube(Connector):
    name = "youtube"
    label = "YouTube"
    searchable = True
    terms_url = "https://www.youtube.com/t/terms"
    docs_url = "https://developers.google.com/youtube/v3/docs"
    description = "Búsqueda y metadatos. NO permite importar: sus condiciones prohíben la descarga."

    def status(self) -> dict[str, Any]:
        return {"available": True, "importable": False,
                "search": bool(settings.youtube_api_key),
                "reason": None if settings.youtube_api_key else "La búsqueda requiere YOUTUBE_API_KEY (YouTube Data API v3)."}

    def can_handle(self, url: str) -> bool:
        host = urlsplit(url).hostname or ""
        return host.endswith("youtube.com") or host == "youtu.be" or host.endswith("youtube-nocookie.com")

    @staticmethod
    def video_id(url: str) -> str | None:
        m = re.search(r"(?:v=|youtu\.be/|/shorts/|/embed/|/live/)([\w-]{11})", url)
        return m.group(1) if m else None

    def _cand(self, vid: str, title: str = "", author: str = "", thumb: str = "") -> VideoCandidate:
        return VideoCandidate(connector=self.name, id=vid, title=title or f"Vídeo de YouTube {vid}",
                              page_url=f"https://www.youtube.com/watch?v={vid}", platform=self.label, author=author,
                              thumbnail_url=thumb or f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg",
                              embed_url=f"https://www.youtube-nocookie.com/embed/{vid}", importable=False,
                              reason=YT_REASON, alternatives=list(YT_ALTERNATIVES),
                              license=LicenseInfo(name="Licencia estándar de YouTube", code="youtube-standard",
                                                  allows_modification=False,
                                                  notes=["Requiere autorización del titular para reutilizarlo."]))

    def resolve(self, url: str) -> VideoCandidate:
        vid = self.video_id(url)
        if not vid:
            raise AppError("No se reconoce la dirección como un vídeo de YouTube.", code="url_no_reconocida")
        cand = self._cand(vid)
        if settings.youtube_api_key:
            data = net.get_json("https://www.googleapis.com/youtube/v3/videos?" + urlencode(
                {"part": "snippet,contentDetails,status", "id": vid, "key": settings.youtube_api_key}))
            items = data.get("items") or []
            if items:
                it = items[0]
                sn = it.get("snippet", {})
                cand.title, cand.author = sn.get("title", cand.title), sn.get("channelTitle", "")
                cand.duration = _iso8601_duration((it.get("contentDetails") or {}).get("duration"))
                if (it.get("status") or {}).get("license") == "creativeCommon":
                    cand.license = cc_license("CC BY 3.0", "https://creativecommons.org/licenses/by/3.0/")
                    cand.license.notes.append("Aunque la licencia sea CC BY, YouTube no permite descargarlo desde la "
                                              "plataforma: pida el archivo al autor.")
        else:
            try:
                o = net.get_json("https://www.youtube.com/oembed?" + urlencode({"url": cand.page_url, "format": "json"}))
                cand.title, cand.author = o.get("title", cand.title), o.get("author_name", "")
                cand.author_url = o.get("author_url", "")
            except AppError:
                pass
        return cand

    def search(self, query: str, page: int = 1, open_only: bool = True) -> list[VideoCandidate]:
        if not settings.youtube_api_key:
            raise ProviderUnavailable("La búsqueda en YouTube requiere YOUTUBE_API_KEY.")
        params = {"part": "snippet", "type": "video", "q": query, "maxResults": 15, "key": settings.youtube_api_key}
        if open_only:
            params["videoLicense"] = "creativeCommon"
        data = net.get_json("https://www.googleapis.com/youtube/v3/search?" + urlencode(params))
        out = []
        for it in data.get("items", []):
            vid = (it.get("id") or {}).get("videoId")
            if vid:
                sn = it.get("snippet", {})
                out.append(self._cand(vid, sn.get("title", ""), sn.get("channelTitle", ""),
                                      ((sn.get("thumbnails") or {}).get("medium") or {}).get("url", "")))
        return out


# ---------------------------------------------------------------- URL directa

class DirectURL(Connector):
    name = "direct_url"
    label = "Enlace directo a un archivo"
    method = "A"
    description = "Enlace https a un archivo de vídeo (MP4, MOV, WEBM…) cuya descarga esté permitida."

    def can_handle(self, url: str) -> bool:
        return True

    def resolve(self, url: str) -> VideoCandidate:
        net.validate_url(url)
        r = net.request(url, headers={"Range": "bytes=0-0"})
        try:
            ctype = (r.headers.get("content-type") or "").split(";")[0].lower()
            size = None
            cr = r.headers.get("content-range", "")
            if "/" in cr and cr.rsplit("/", 1)[1].isdigit():
                size = int(cr.rsplit("/", 1)[1])
            elif r.headers.get("content-length", "").isdigit() and r.status == 200:
                size = int(r.headers["content-length"])
            final = r.url
            status = r.status
        finally:
            r.close()
        name = unquote(urlsplit(final).path.rsplit("/", 1)[-1]) or "video"
        cand = VideoCandidate(connector=self.name, id=final, title=name[:120], page_url=url,
                              platform=urlsplit(final).hostname or "", format=ctype, size_bytes=size, method="A",
                              license=LicenseInfo(notes=["Enlace sin información de licencia: indique con qué derecho lo usa."]))
        if status >= 400:
            cand.reason = f"El servidor no permite acceder al archivo (error {status})."
        elif ctype.startswith("text/html") or ctype.startswith("application/xhtml"):
            cand.reason = ("La dirección es una página web, no un archivo de vídeo. Si la plataforma ofrece un botón de "
                           "descarga, úselo y luego importe el archivo; no se extraen vídeos de páginas.")
        elif ctype and not any(ctype.startswith(a) for a in net.VIDEO_CONTENT_TYPES):
            cand.reason = f"El enlace no apunta a un vídeo (tipo «{ctype}»)."
        elif ctype in ("application/vnd.apple.mpegurl", "application/x-mpegurl", "application/dash+xml"):
            cand.reason = "Es un flujo de reproducción (HLS/DASH), no un archivo descargable."
        else:
            cand.importable = True
            cand.file_url = final
        if size and size > settings.max_upload_mb * 1024 * 1024:
            cand.importable = False
            cand.reason = f"El archivo supera el máximo de {settings.max_upload_mb} MB."
        return cand
