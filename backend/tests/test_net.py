"""Red de salida segura (anti‑SSRF). Protege la descarga de los resultados de fal.ai y cualquier
otra petición saliente: sólo URLs públicas, redirecciones validadas y archivos con firma multimedia.
Se usa un servidor HTTP local real; sólo 127.0.0.1 se permite durante las pruebas."""
from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from app import net
from app.errors import AppError, LimitExceeded
from app.gen import fal

from .conftest import make_video

# ------------------------------------------------------------------ servidor HTTP local

class _Handler(BaseHTTPRequestHandler):
    routes: dict[str, tuple[int, dict[str, str], bytes]] = {}

    def do_GET(self):  # noqa: N802
        status, headers, body = self.routes.get(self.path.split("?")[0], (404, {}, b"no"))
        self.send_response(status)
        for k, v in headers.items():
            self.send_header(k, v)
        if "Content-Length" not in headers:
            self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


@pytest.fixture()
def server():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    _Handler.routes = {}
    yield f"http://127.0.0.1:{srv.server_address[1]}", _Handler.routes
    srv.shutdown()


@pytest.fixture()
def local_ok(env, monkeypatch):
    """Permite SOLO 127.0.0.1 (el servidor de pruebas); el resto de IPs privadas sigue bloqueado."""
    real = net._ip_allowed
    monkeypatch.setattr(net, "_ip_allowed", lambda ip: ip == "127.0.0.1" or real(ip))
    monkeypatch.setattr(net, "ALLOWED_PORTS", {None, 80, 443} | set(range(1024, 65536)))


# ------------------------------------------------------------------ validación de URL (SSRF)

@pytest.mark.parametrize("url", [
    "http://127.0.0.1/a.mp4", "http://169.254.169.254/latest/meta-data/", "http://[::1]/", "http://10.1.2.3/",
    "http://192.168.1.1/", "http://172.16.0.5/", "http://100.64.0.1/", "http://0.0.0.0/", "http://[fd00::1]/",
    "http://[::ffff:127.0.0.1]/", "https://localhost/", "https://metadata.google.internal/", "https://nas.local/",
    "file:///etc/passwd", "ftp://example.com/a.mp4", "gopher://example.com/", "javascript:alert(1)",
    "https://user:pass@example.com/a.mp4", "https://example.com:22/", "http://2130706433/",
])
def test_blocked_urls(env, url):
    with pytest.raises(AppError):
        net.validate_url(url)


def test_hostname_resolving_to_private_ip_is_blocked(env, monkeypatch):
    monkeypatch.setattr(net, "resolve", lambda host, port: ["10.0.0.7"])
    with pytest.raises(net.UnsafeURL, match="red privada"):
        net.validate_url("https://inofensivo.example.com/video.mp4")
    monkeypatch.setattr(net, "resolve", lambda host, port: ["93.184.216.34", "127.0.0.1"])
    with pytest.raises(net.UnsafeURL):
        net.validate_url("https://mixto.example.com/video.mp4")
    monkeypatch.setattr(net, "resolve", lambda host, port: ["93.184.216.34"])
    t = net.validate_url("https://publico.example.com/video.mp4")
    assert t.ip == "93.184.216.34"  # la conexión se fija a esta IP (anti DNS rebinding)


def test_redirect_to_internal_address_is_blocked(server, local_ok):
    base, routes = server
    routes["/r"] = (302, {"Location": "http://169.254.169.254/latest/meta-data/"}, b"")
    with pytest.raises(net.UnsafeURL):
        net.download(f"{base}/r", Path("/tmp/no-se-usa.mp4"), max_bytes=10_000)


def test_too_many_redirects(server, local_ok):
    base, routes = server
    routes["/loop"] = (302, {"Location": "/loop"}, b"")
    with pytest.raises(net.UnsafeURL, match="redirecciones"):
        net.request(f"{base}/loop")


# ------------------------------------------------------------------ descarga segura

def test_download_video_ok(server, local_ok, tmp_path):
    base, routes = server
    data = make_video(tmp_path / "v.mp4", seconds=1).read_bytes()
    routes["/v.mp4"] = (200, {"Content-Type": "video/mp4"}, data)
    res = net.download(f"{base}/v.mp4", tmp_path / "out.mp4", max_bytes=10 * 1024 * 1024)
    assert res["container"] == "mp4/mov" and res["bytes"] == len(data)


@pytest.mark.parametrize("ctype,body", [
    ("text/html", b"<!doctype html><html>video</html>"),
    ("video/mp4", b"MZ\x90\x00 ejecutable de Windows disfrazado" + b"\x00" * 100),
    ("video/mp4", b"\x7fELF ejecutable" + b"\x00" * 100),
    ("application/octet-stream", b"PK\x03\x04 zip" + b"\x00" * 100),
    ("video/mp4", b"#EXTM3U\n#EXTINF:1,\nhttp://10.0.0.1/x\n"),
    ("application/x-msdownload", b"MZ"),
    ("video/mp4", b"datos aleatorios sin firma de video" * 10),
])
def test_download_rejects_non_video(server, local_ok, tmp_path, ctype, body):
    base, routes = server
    routes["/x"] = (200, {"Content-Type": ctype}, body)
    with pytest.raises(AppError):
        net.download(f"{base}/x", tmp_path / "x.mp4", max_bytes=10_000_000)
    assert not (tmp_path / "x.mp4").exists() and not (tmp_path / "x.mp4.part").exists()


def test_download_size_limits(server, local_ok, tmp_path):
    base, routes = server
    data = make_video(tmp_path / "v.mp4", seconds=2).read_bytes()
    routes["/big"] = (200, {"Content-Type": "video/mp4"}, data)
    with pytest.raises(LimitExceeded):  # declarado en Content-Length
        net.download(f"{base}/big", tmp_path / "o.mp4", max_bytes=1000)
    routes["/liar"] = (200, {"Content-Type": "video/mp4", "Content-Length": str(len(data))}, data)
    with pytest.raises(LimitExceeded):
        net.download(f"{base}/liar", tmp_path / "o2.mp4", max_bytes=len(data) - 1)


def test_download_forbidden_source(server, local_ok, tmp_path):
    base, routes = server
    routes["/drm"] = (403, {"Content-Type": "text/plain"}, b"forbidden")
    with pytest.raises(AppError, match="no permite"):
        net.download(f"{base}/drm", tmp_path / "o.mp4", max_bytes=1000)




# ------------------------------------------------------------------ resultados de fal.ai

def test_fal_download_blocks_internal_redirect_and_non_media(server, local_ok, tmp_path):
    base, routes = server
    routes["/r"] = (302, {"Location": "http://169.254.169.254/latest/meta-data/"}, b"")
    with pytest.raises(net.UnsafeURL):
        fal.download(f"{base}/r", tmp_path / "a.jpg")
    routes["/page"] = (200, {"Content-Type": "text/html"}, b"<html>no</html>")
    with pytest.raises(AppError, match="no es un archivo multimedia"):
        fal.download(f"{base}/page", tmp_path / "b.jpg")
    with pytest.raises(net.UnsafeURL):
        fal.download("http://10.0.0.5/imagen.jpg", tmp_path / "c.jpg")
