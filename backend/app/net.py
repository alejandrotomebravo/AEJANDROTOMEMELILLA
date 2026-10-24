"""Cliente HTTP seguro para importar contenido de Internet.

Protecciones:

* Sólo ``http``/``https`` en los puertos 80/443, sin credenciales en la URL.
* **Anti-SSRF**: se resuelve el nombre y se rechaza cualquier dirección que no
  sea pública (privadas, bucle local, enlace local — incluida la de metadatos
  de nube 169.254.169.254 —, multidifusión, reservadas, CGNAT, IPv6 ULA…). La
  conexión se fija a la IP validada (evita *DNS rebinding*) y cada redirección
  se valida de nuevo.
* Límites de tamaño, de tiempo y de número de redirecciones.
* Lista blanca de tipos de contenido y comprobación de la firma binaria del
  archivo: se rechazan ejecutables, archivos comprimidos, HTML, PDF, scripts…
* Nunca se envían cookies ni credenciales de usuario a terceros.

Si el servidor sale a Internet a través de un proxy (OUTBOUND_PROXY), la
resolución DNS puede hacerla el proxy; en ese caso la aplicación sigue
bloqueando IPs literales y nombres internos, y el proxy debe imponer además su
propia política de salida (ver docs/SEGURIDAD.md).
"""
from __future__ import annotations

import base64
import http.client
import ipaddress
import json
import os
import socket
import ssl
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urljoin, urlsplit

from .config import settings
from .errors import AppError, JobCancelled, LimitExceeded, RetryableError


class UnsafeURL(AppError):
    status = 400
    code = "url_no_permitida"


BLOCKED_HOSTNAMES = ("localhost", "localhost.localdomain", "metadata.google.internal", "metadata")
BLOCKED_SUFFIXES = (".localhost", ".local", ".internal", ".intranet", ".lan", ".home.arpa", ".corp")
ALLOWED_PORTS = {None, 80, 443}

VIDEO_CONTENT_TYPES = ("video/", "application/octet-stream", "application/ogg", "binary/octet-stream",
                       "application/mp4", "application/x-matroska")
REJECT_CONTENT_TYPES = ("text/html", "application/xhtml", "application/x-msdownload", "application/x-msdos",
                        "application/x-executable", "application/x-sh", "application/zip", "application/x-rar",
                        "application/pdf", "application/javascript", "application/x-apple-diskimage",
                        "application/vnd.apple.mpegurl", "application/x-mpegurl", "application/dash+xml")


@dataclass
class Target:
    url: str
    scheme: str
    host: str
    port: int
    path: str
    ip: str | None  # None: resolución delegada en el proxy


def _ip_allowed(ip: str) -> bool:
    addr = ipaddress.ip_address(ip)
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped:
        addr = addr.ipv4_mapped
    if settings.allow_private_networks:
        return not addr.is_multicast and not addr.is_unspecified
    return addr.is_global and not (addr.is_multicast or addr.is_reserved or addr.is_loopback
                                   or addr.is_link_local or addr.is_private or addr.is_unspecified)


def resolve(host: str, port: int) -> list[str]:
    infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    return sorted({i[4][0] for i in infos})


def validate_url(url: str) -> Target:
    url = (url or "").strip()
    if len(url) > 2048:
        raise UnsafeURL("La URL es demasiado larga.")
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise UnsafeURL("Sólo se admiten direcciones http:// o https://.")
    if parts.username or parts.password:
        raise UnsafeURL("La URL no puede contener usuario ni contraseña.")
    host = (parts.hostname or "").rstrip(".").lower()
    if not host:
        raise UnsafeURL("La URL no indica un servidor.")
    try:
        port = parts.port
    except ValueError:
        raise UnsafeURL("Puerto no válido.")
    if port not in ALLOWED_PORTS:
        raise UnsafeURL("Sólo se permiten los puertos estándar 80 y 443.")
    port = port or (443 if parts.scheme == "https" else 80)
    if host in BLOCKED_HOSTNAMES or host.endswith(BLOCKED_SUFFIXES):
        raise UnsafeURL("No se permite acceder a direcciones internas.")
    path = parts.path or "/"
    if parts.query:
        path += "?" + parts.query
    literal = None
    try:
        literal = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        pass
    if literal is not None:
        if not _ip_allowed(str(literal)):
            raise UnsafeURL("No se permite acceder a direcciones IP privadas o internas.")
        return Target(url, parts.scheme, host.strip("[]"), port, path, str(literal))
    try:
        ips = resolve(host, port)
    except socket.gaierror:
        if settings.outbound_proxy:
            return Target(url, parts.scheme, host, port, path, None)
        raise UnsafeURL("No se encuentra el servidor indicado en la URL.")
    if not ips or not all(_ip_allowed(ip) for ip in ips):
        raise UnsafeURL("El servidor de la URL apunta a una red privada o interna.")
    return Target(url, parts.scheme, host, port, path, ips[0])


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, host: str, ip: str, port: int, context: ssl.SSLContext, timeout: float):
        super().__init__(host, port, context=context, timeout=timeout)
        self._pinned_ip = ip

    def connect(self) -> None:
        sock = socket.create_connection((self._pinned_ip, self.port), self.timeout)
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


class _PinnedHTTPConnection(http.client.HTTPConnection):
    def __init__(self, host: str, ip: str, port: int, timeout: float):
        super().__init__(host, port, timeout=timeout)
        self._pinned_ip = ip

    def connect(self) -> None:
        self.sock = socket.create_connection((self._pinned_ip, self.port), self.timeout)


def _ssl_context() -> ssl.SSLContext:
    cafile = os.environ.get("SSL_CERT_FILE") or os.environ.get("REQUESTS_CA_BUNDLE")
    return ssl.create_default_context(cafile=cafile if cafile and Path(cafile).is_file() else None)


def _connection(t: Target) -> http.client.HTTPConnection:
    timeout = settings.download_timeout_seconds
    if settings.outbound_proxy:
        p = urlsplit(settings.outbound_proxy)
        headers = {}
        if p.username:
            cred = base64.b64encode(f"{p.username}:{p.password or ''}".encode()).decode()
            headers["Proxy-Authorization"] = f"Basic {cred}"
        # Proxy HTTP: túnel CONNECT para destinos https (TLS extremo a extremo con el destino).
        if t.scheme == "https":
            conn: http.client.HTTPConnection = http.client.HTTPSConnection(
                p.hostname, p.port or 3128, timeout=timeout, context=_ssl_context())
            conn.set_tunnel(t.host, t.port, headers=headers)
        else:
            conn = http.client.HTTPConnection(p.hostname, p.port or 3128, timeout=timeout)
            conn._proxy_headers = headers  # type: ignore[attr-defined]
        return conn
    if t.ip is None:
        raise UnsafeURL("No se pudo validar la dirección del servidor.")
    if t.scheme == "https":
        return _PinnedHTTPSConnection(t.host, t.ip, t.port, _ssl_context(), timeout)
    return _PinnedHTTPConnection(t.host, t.ip, t.port, timeout)


class Response:
    def __init__(self, conn: http.client.HTTPConnection, resp: http.client.HTTPResponse, url: str):
        self._conn = conn
        self.raw = resp
        self.status = resp.status
        self.url = url
        self.headers = {k.lower(): v for k, v in resp.getheaders()}

    def read(self, n: int = -1) -> bytes:
        return self.raw.read(n)

    def close(self) -> None:
        self._conn.close()


def request(url: str, *, method: str = "GET", headers: dict[str, str] | None = None) -> Response:
    """Petición con validación anti-SSRF en cada salto de redirección."""
    current = url
    for _ in range(settings.max_redirects + 1):
        t = validate_url(current)
        conn = _connection(t)
        hdrs = {"User-Agent": settings.http_user_agent, "Accept": "*/*", "Accept-Encoding": "identity"}
        hdrs.update(headers or {})
        hdrs.update(getattr(conn, "_proxy_headers", {}))
        try:
            path = t.path if not (settings.outbound_proxy and t.scheme == "http") else t.url
            conn.request(method, path, headers=hdrs)
            resp = conn.getresponse()
        except (OSError, http.client.HTTPException) as e:
            conn.close()
            raise RetryableError(f"No se pudo conectar con el servidor de origen ({type(e).__name__}).")
        if resp.status in (301, 302, 303, 307, 308):
            loc = resp.getheader("Location")
            conn.close()
            if not loc:
                raise AppError("El servidor de origen devolvió una redirección no válida.", code="descarga")
            current = urljoin(current, loc)
            continue
        return Response(conn, resp, current)
    raise UnsafeURL("Demasiadas redirecciones.")


def get_json(url: str, headers: dict[str, str] | None = None, max_bytes: int = 5 * 1024 * 1024) -> Any:
    r = request(url, headers={"Accept": "application/json", **(headers or {})})
    try:
        if r.status in (429, 500, 502, 503, 504):
            raise RetryableError(f"El servicio de origen no está disponible ahora ({r.status}).")
        if r.status >= 400:
            raise AppError(f"El servicio de origen respondió con un error ({r.status}).", code="fuente_error",
                           status=502)
        data = r.read(max_bytes + 1)
        if len(data) > max_bytes:
            raise LimitExceeded("Respuesta demasiado grande.")
        return json.loads(data.decode("utf-8"))
    finally:
        r.close()


def sniff(head: bytes) -> str | None:
    """Identifica contenedores de vídeo por su firma. None = no reconocido."""
    if len(head) >= 12 and head[4:8] == b"ftyp":
        return "mp4/mov"
    if head.startswith(b"\x1a\x45\xdf\xa3"):
        return "matroska/webm"
    if head.startswith(b"OggS"):
        return "ogg"
    if head.startswith(b"RIFF") and head[8:12] == b"AVI ":
        return "avi"
    if head[:1] == b"\x47" and len(head) > 188 and head[188:189] == b"\x47":
        return "mpegts"
    if len(head) >= 8 and head[4:8] in (b"moov", b"mdat", b"wide", b"free", b"skip"):
        return "mp4/mov"
    return None


DANGEROUS = (b"MZ", b"\x7fELF", b"PK\x03\x04", b"%PDF", b"#!", b"\xca\xfe\xba\xbe", b"\xcf\xfa\xed\xfe",
             b"Rar!", b"7z\xbc\xaf")


def download(url: str, dest: Path, *, max_bytes: int, headers: dict[str, str] | None = None,
             on_progress: Callable[[float], None] | None = None,
             should_cancel: Callable[[], bool] | None = None) -> dict[str, Any]:
    r = request(url, headers=headers)
    try:
        if r.status in (429, 500, 502, 503, 504):
            raise RetryableError(f"El servidor de origen no está disponible ahora ({r.status}).")
        if r.status in (401, 403):
            raise AppError("La fuente no permite descargar este archivo (acceso restringido).", code="descarga_denegada",
                           status=403)
        if r.status >= 400:
            raise AppError(f"No se pudo descargar el archivo (error {r.status}).", code="descarga", status=502)
        ctype = (r.headers.get("content-type") or "").split(";")[0].strip().lower()
        if any(ctype.startswith(b) for b in REJECT_CONTENT_TYPES):
            raise UnsafeURL(f"La URL no apunta a un archivo de vídeo (tipo «{ctype}»). Si es una página web, "
                            "use el conector de su plataforma o descargue el vídeo desde la propia fuente.")
        if ctype and not any(ctype.startswith(a) for a in VIDEO_CONTENT_TYPES):
            raise UnsafeURL(f"Tipo de archivo no admitido: «{ctype}».")
        length = int(r.headers.get("content-length") or 0)
        if length and length > max_bytes:
            raise LimitExceeded(f"El archivo ({length // 1048576} MB) supera el máximo de {max_bytes // 1048576} MB.")
        written = 0
        tmp = dest.with_suffix(dest.suffix + ".part")
        kind = None
        try:
            with open(tmp, "wb") as f:
                while True:
                    if should_cancel and should_cancel():
                        raise JobCancelled()
                    chunk = r.read(256 * 1024)
                    if not chunk:
                        break
                    if written == 0:
                        if chunk.startswith(DANGEROUS) or chunk.lstrip()[:15].lower().startswith((b"<!doctype", b"<html", b"<?xml", b"#extm3u")):
                            raise UnsafeURL("El archivo descargado no es un vídeo (contenido ejecutable, comprimido o web).")
                        kind = sniff(chunk[:4096])
                        if kind is None:
                            raise UnsafeURL("El archivo descargado no tiene la firma de un formato de vídeo admitido.")
                    written += len(chunk)
                    if written > max_bytes:
                        raise LimitExceeded(f"El archivo supera el máximo de {max_bytes // 1048576} MB.")
                    f.write(chunk)
                    if on_progress and length:
                        on_progress(written / length)
            if written == 0:
                raise AppError("El servidor devolvió un archivo vacío.", code="descarga")
            tmp.replace(dest)
        finally:
            if tmp.exists():
                tmp.unlink()
        return {"bytes": written, "content_type": ctype, "container": kind, "final_url": r.url}
    finally:
        r.close()
