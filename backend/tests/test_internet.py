"""Extractor de Internet: seguridad (SSRF, tipos de archivo, tamaños) y conectores.

Las API reales (Wikimedia, Internet Archive, Pexels, Vimeo, YouTube) se simulan
con respuestas JSON con la estructura de su documentación oficial: el entorno
de pruebas no tiene acceso a esos servicios. Las descargas se hacen de verdad
contra un servidor HTTP local.
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from app import net, policy
from app.errors import AppError, LimitExceeded
from app.jobs import queue
from app.sources import connectors, registry

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


# ------------------------------------------------------------------ conectores (respuestas simuladas)

COMMONS_PAGE = {
    "title": "File:Faro de Melilla.webm", "index": 1,
    "imageinfo": [{
        "url": "https://upload.wikimedia.org/wikipedia/commons/a/ab/Faro_de_Melilla.webm",
        "descriptionurl": "https://commons.wikimedia.org/wiki/File:Faro_de_Melilla.webm",
        "thumburl": "https://upload.wikimedia.org/thumb.jpg", "mediatype": "VIDEO", "mime": "video/webm",
        "size": 1234567, "width": 1280, "height": 720,
        "extmetadata": {"LicenseShortName": {"value": "CC BY-SA 4.0"},
                        "LicenseUrl": {"value": "https://creativecommons.org/licenses/by-sa/4.0"},
                        "Artist": {"value": "<a href='//commons.wikimedia.org/wiki/User:Autora'>Autora Ejemplo</a>"},
                        "AttributionRequired": {"value": "true"}}},
    ]}


def test_wikimedia_connector(env, monkeypatch):
    calls = []

    def fake(url, headers=None):
        calls.append(url)
        return {"query": {"pages": [COMMONS_PAGE, {"title": "File:foto.jpg", "imageinfo": [{"mediatype": "BITMAP"}]}]}}
    monkeypatch.setattr(net, "get_json", fake)
    c = connectors.WikimediaCommons()
    res = c.search("faro melilla")
    assert len(res) == 1
    v = res[0]
    assert v.importable and v.author == "Autora Ejemplo" and v.license.code == "cc-by-sa"
    assert v.license.requires_attribution and v.license.share_alike
    assert "Autora Ejemplo" in v.attribution and "CC BY-SA 4.0" in v.attribution
    assert "filetype%3Avideo" in calls[0]
    assert "file_url" not in v.as_dict()  # el enlace del archivo no se envía al navegador
    assert registry.for_url("https://commons.wikimedia.org/wiki/File:Faro_de_Melilla.webm").name == "wikimedia"


def test_wikimedia_nd_not_importable(env, monkeypatch):
    page = json.loads(json.dumps(COMMONS_PAGE))
    page["imageinfo"][0]["extmetadata"]["LicenseShortName"]["value"] = "CC BY-ND 4.0"
    page["imageinfo"][0]["extmetadata"]["LicenseUrl"]["value"] = "https://creativecommons.org/licenses/by-nd/4.0"
    monkeypatch.setattr(net, "get_json", lambda url, headers=None: {"query": {"pages": [page]}})
    v = connectors.WikimediaCommons().resolve("https://commons.wikimedia.org/wiki/File:Faro_de_Melilla.webm")
    assert not v.importable and "ND" in v.reason


IA_META = {
    "metadata": {"identifier": "pelicula_1950", "title": "Película de 1950", "creator": "Estudio X",
                 "licenseurl": "http://creativecommons.org/publicdomain/mark/1.0/"},
    "files": [
        {"name": "pelicula.ogv", "format": "Ogg Video", "source": "derivative", "size": "100", "height": "480"},
        {"name": "pelicula.mp4", "format": "h.264", "source": "derivative", "size": "200", "height": "720",
         "length": "61.2"},
        {"name": "pelicula_meta.xml", "format": "Metadata", "source": "original"},
    ]}


def test_internet_archive_connector(env, monkeypatch):
    monkeypatch.setattr(net, "get_json", lambda url, headers=None: IA_META)
    v = connectors.InternetArchive().resolve("https://archive.org/details/pelicula_1950")
    assert v.importable and v.file_url.endswith("/pelicula_1950/pelicula.mp4")
    assert v.license.code == "public-domain" and v.duration == 61.2 and v.height == 720
    restricted = json.loads(json.dumps(IA_META))
    restricted["metadata"]["access-restricted-item"] = "true"
    monkeypatch.setattr(net, "get_json", lambda url, headers=None: restricted)
    v = connectors.InternetArchive().resolve("https://archive.org/details/pelicula_1950")
    assert not v.importable and "préstamo" in v.reason


def test_internet_archive_without_license_warns(env, monkeypatch):
    meta = json.loads(json.dumps(IA_META))
    del meta["metadata"]["licenseurl"]
    monkeypatch.setattr(net, "get_json", lambda url, headers=None: meta)
    v = connectors.InternetArchive().resolve("pelicula_1950")
    assert v.license.code == "unknown" and any("licencia" in n for n in v.license.notes)


def test_pexels_requires_key_and_parses(env, monkeypatch):
    c = connectors.Pexels()
    assert not c.status()["available"]
    monkeypatch.setattr(env, "pexels_api_key", "clave")
    seen = {}

    def fake(url, headers=None):
        seen["auth"] = headers.get("Authorization")
        return {"id": 42, "url": "https://www.pexels.com/video/olas-42/", "duration": 12, "image": "https://img",
                "user": {"name": "Fotógrafa", "url": "https://www.pexels.com/@f"},
                "video_files": [{"file_type": "video/mp4", "width": 3840, "height": 2160, "link": "https://videos.pexels.com/4k.mp4"},
                                {"file_type": "video/mp4", "width": 1920, "height": 1080, "link": "https://videos.pexels.com/hd.mp4"}]}
    monkeypatch.setattr(net, "get_json", fake)
    v = c.resolve("https://www.pexels.com/video/olas-42/")
    assert seen["auth"] == "clave" and v.file_url.endswith("hd.mp4") and v.importable
    assert any("consentimiento" in n for n in v.license.notes)


def test_vimeo_only_with_download_permission(env, monkeypatch):
    c = connectors.Vimeo()
    v = c.resolve("https://vimeo.com/123456")
    assert not v.importable and "token" in v.reason.lower()
    monkeypatch.setattr(env, "vimeo_access_token", "tok")
    monkeypatch.setattr(net, "get_json", lambda url, headers=None: {"name": "Mi vídeo", "user": {"name": "Yo"},
                                                                      "license": None, "download": []})
    v = c.resolve("https://vimeo.com/123456")
    assert not v.importable and "propietario" in v.reason
    monkeypatch.setattr(net, "get_json", lambda url, headers=None: {
        "name": "Mi vídeo", "user": {"name": "Yo"}, "license": "by", "duration": 30,
        "download": [{"quality": "hd", "type": "video/mp4", "height": 1080, "size": 10, "link": "https://vimeo.example/dl"}]})
    v = c.resolve("https://vimeo.com/123456")
    assert v.importable and v.license.code == "cc-by" and c.download_headers()["Authorization"] == "bearer tok"


def test_youtube_never_importable(env, monkeypatch):
    monkeypatch.setattr(net, "get_json", lambda url, headers=None: {"title": "Vídeo", "author_name": "Canal"})
    v = registry.resolve("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
    assert v.connector == "youtube" and not v.importable and v.alternatives
    assert "YouTube Studio" in " ".join(v.alternatives)
    assert registry.resolve("https://youtu.be/dQw4w9WgXcQ").id == "dQw4w9WgXcQ"


def test_direct_url_detects_web_pages(server, local_ok):
    base, routes = server
    routes["/pagina"] = (200, {"Content-Type": "text/html"}, b"<html></html>")
    v = registry.resolve(f"{base}/pagina")
    assert v.connector == "direct_url" and not v.importable and "página web" in v.reason


# ------------------------------------------------------------------ importación de extremo a extremo

def _project(client, auth):
    pid = client.post("/api/projects", json={"name": "Internet", "aspect": "16:9"}, headers=auth).json()["id"]
    client.put(f"/api/projects/{pid}/use-declaration",
               json={"purpose": "educativo", "distribution": "redes",
                     "confirmations": {k: True for k in policy.REQUIRED_CONFIRMATIONS}}, headers=auth)
    return pid


def test_import_from_wikimedia_end_to_end(client, auth, server, local_ok, monkeypatch, tmp_path):
    base, routes = server
    data = make_video(tmp_path / "faro.mp4", seconds=4).read_bytes()
    routes["/commons/faro.webm"] = (200, {"Content-Type": "video/webm"}, data)
    page = json.loads(json.dumps(COMMONS_PAGE))
    page["imageinfo"][0]["url"] = f"{base}/commons/faro.webm"  # archivo servido localmente
    monkeypatch.setattr(net, "get_json", lambda url, headers=None: {"query": {"pages": [page]}})
    pid = _project(client, auth)
    url = "https://commons.wikimedia.org/wiki/File:Faro_de_Melilla.webm"
    info = client.post("/api/internet/resolve", json={"url": url}, headers=auth).json()
    assert info["importable"] and info["license"]["code"] == "cc-by-sa"
    # sin permiso de modificación no se importa
    r = client.post(f"/api/projects/{pid}/sources/internet", json={"url": url, "provenance": {
        "source_type": "dominio_publico_cc", "attested": True}}, headers=auth)
    assert r.status_code == 422
    r = client.post(f"/api/projects/{pid}/sources/internet", json={"url": url, "start": 1, "end": 3, "provenance": {
        "source_type": "dominio_publico_cc", "attested": True, "can_modify": True, "can_distribute": True}},
        headers=auth)
    assert r.status_code == 200, r.text
    assert "file_url" not in r.json()["job"]["params"]
    queue.run_pending()
    job = client.get(f"/api/jobs/{r.json()['job']['id']}", headers=auth).json()
    assert job["status"] == "succeeded", job
    proj = client.get(f"/api/projects/{pid}", headers=auth).json()
    src = proj["sources"][0]
    assert src["kind"] == "internet" and abs(src["duration"] - 2.0) < 0.15
    assert src["origin"]["page_url"].startswith("https://commons.wikimedia.org") and src["origin"]["imported_at"]
    assert src["provenance"]["license_url"].startswith("https://creativecommons.org")
    assert proj["credits_required"] and "Autora Ejemplo" in proj["credits"][0]
    hist = client.get("/api/internet/history", headers=auth).json()
    assert hist[0]["platform"] == "Wikimedia Commons" and hist[0]["license"] == "CC BY-SA 4.0"
    est = client.post(f"/api/projects/{pid}/estimate", json={"resolution": "720p", "fps": 25}, headers=auth).json()
    assert any("misma licencia" in w for w in est["rights_warnings"])
    # Exportación con créditos en pantalla y en los metadatos
    r = client.post(f"/api/projects/{pid}/export", json={"resolution": "720p", "fps": 25, "ack_disclaimer": True},
                    headers=auth)
    queue.run_pending()
    job = client.get(f"/api/jobs/{r.json()['job']['id']}", headers=auth).json()
    assert job["status"] == "succeeded", job
    assert "Autora Ejemplo" in job["result"]["credits"][0]
    from app import storage
    manifest = json.loads(Path(storage.get(job["result"]["manifest_asset_id"])["path"]).read_text())
    assert manifest["materiales"][0]["licencia"] == "CC BY-SA 4.0" and manifest["materiales"][0]["sha256_original"]


def test_import_refuses_youtube(client, auth, monkeypatch, env):
    monkeypatch.setattr(net, "get_json", lambda url, headers=None: {"title": "x"})
    pid = _project(client, auth)
    r = client.post(f"/api/projects/{pid}/sources/internet", json={
        "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "provenance": {"source_type": "autorizado", "attested": True, "can_modify": True, "rights_holder": "x"}},
        headers=auth)
    assert r.status_code == 422 and r.json()["error"] == "no_importable"
    assert r.json()["details"]["alternatives"]


def test_import_blocks_ssrf_through_api(client, auth, env):
    pid = _project(client, auth)
    r = client.post("/api/internet/resolve", json={"url": "http://169.254.169.254/latest/meta-data/"}, headers=auth)
    assert r.status_code == 400 and r.json()["error"] == "url_no_permitida"
    r = client.post(f"/api/projects/{pid}/sources/internet", json={
        "url": "http://127.0.0.1:8000/api/system/health", "provenance": {}}, headers=auth)
    assert r.status_code == 400
