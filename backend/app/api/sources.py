"""Fuentes de vídeo de un proyecto: archivos locales (por lotes) e importación desde Internet."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, Form, UploadFile
from pydantic import BaseModel

from .. import db, policy, services, storage
from ..errors import AppError, LimitExceeded, NotFound, PolicyError
from ..jobs import queue
from ..media import ffmpeg, timeline
from ..sources import registry
from .deps import current_user

router = APIRouter(prefix="/api", tags=["fuentes"])

VIDEO_EXT = {".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi", ".3gp", ".ogv", ".mpg", ".mpeg", ".ts"}
MAX_BATCH = 20


def source_out(s: dict[str, Any]) -> dict[str, Any]:
    o = s["origin"] or {}
    n_chars = db.one("SELECT COUNT(*) AS n FROM characters WHERE source_id=?", (s["id"],))["n"]
    return {
        "id": s["id"], "kind": s["kind"], "label": s["label"], "status": s["status"], "error": s["error"],
        "origin": {k: o.get(k) for k in ("connector", "platform", "page_url", "title", "author", "author_url",
                                          "license", "attribution", "imported_at", "thumbnail_url", "embed_url")},
        "provenance": s["provenance"], "fragment": s["fragment"],
        "duration": s["duration"], "width": s["width"], "height": s["height"], "fps": s["fps"],
        "proxy_asset_id": s["proxy_asset_id"], "review_asset_id": s["review_asset_id"],
        "sprite_asset_id": s["sprite_asset_id"],
        "processed": bool(s["working_asset_id"]) and s["working_asset_id"] != s["proxy_asset_id"],
        "history": [{"operation": h["operation"], "at": h["at"], "index": i,
                     "current": h["asset_id"] == s["working_asset_id"]} for i, h in enumerate(s["video_history"])],
        "analysis": s["analysis"], "characters": n_chars, "created_at": s["created_at"],
    }


def _new_source(project_id: str, user_id: str, kind: str, label: str, origin: dict[str, Any],
                provenance: dict[str, Any], fragment: dict[str, Any] | None, original_asset_id: str | None) -> str:
    sid = db.new_id()
    db.execute("""INSERT INTO sources(id,project_id,user_id,kind,label,status,origin,provenance,fragment,original_asset_id,created_at)
                  VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
               (sid, project_id, user_id, kind, label[:120] or "Vídeo", "pending", db.jdump(origin), db.jdump(provenance),
                db.jdump(fragment) if fragment else None, original_asset_id, db.now()))
    services.touch_project(project_id)
    return sid


def _fragment(start: float | None, end: float | None) -> dict[str, float] | None:
    if start is None and end is None:
        return None
    s = float(start or 0)
    e = float(end) if end is not None else None
    if s < 0 or (e is not None and e - s < 0.5):
        raise AppError("El fragmento debe durar al menos 0,5 s.", code="fragmento")
    return {"start": s, "end": e} if e is not None else {"start": s}


@router.get("/projects/{pid}/sources")
def list_sources(pid: str, user=Depends(current_user)) -> list[dict[str, Any]]:
    services.load_project(pid, user["id"])
    return [source_out(s) for s in services.list_sources(pid)]


@router.post("/projects/{pid}/sources/upload")
def upload_sources(pid: str, files: list[UploadFile] = File(...), provenance: str = Form(...),
                   start: float | None = Form(None), end: float | None = Form(None),
                   user=Depends(current_user)) -> dict[str, Any]:
    """Importación de uno o varios archivos (lote) con una misma declaración de procedencia."""
    project = services.load_project(pid, user["id"])
    services.require_declaration(project)
    if len(files) > MAX_BATCH:
        raise LimitExceeded(f"Puede subir como máximo {MAX_BATCH} archivos a la vez.")
    try:
        prov = policy.validate_provenance(json.loads(provenance))
    except json.JSONDecodeError:
        raise AppError("Declaración de procedencia no válida.")
    created = []
    for f in files:
        ext = Path(f.filename or "video.mp4").suffix.lower() or ".mp4"
        if ext not in VIDEO_EXT:
            raise AppError(f"Formato no admitido: {f.filename}. Use MP4, MOV, M4V, WEBM, MKV, AVI o 3GP.")
        aid, dest = storage.new_path(user["id"], ext, project_id=pid)
        storage.save_stream(f.file, dest)
        try:
            info = ffmpeg.probe(dest, untrusted="video")
            if not info.has_video:
                raise AppError("sin vídeo")
        except AppError:
            dest.unlink(missing_ok=True)
            raise AppError(f"«{f.filename}» no es un vídeo válido o su formato no está admitido.", code="video_no_valido")
        a = storage.register(aid=aid, user_id=user["id"], project_id=pid, kind="source", path=dest,
                             mime=f.content_type, duration=info.duration, width=info.width, height=info.height,
                             meta={"filename": (f.filename or "")[:120]})
        label = Path(f.filename or "Vídeo").stem
        sid = _new_source(pid, user["id"], "upload", label, {"platform": "Archivo local", "imported_at": db.now(),
                                                              "sha256": a["sha256"]},
                          prov, _fragment(start, end) if len(files) == 1 else None, a["id"])
        db.execute("UPDATE assets SET meta=? WHERE id=?", (db.jdump(a["meta"] | {"source_id": sid}), aid))
        db.audit("declaracion_procedencia", user["id"], pid, source=sid, **prov)
        job = queue.enqueue("import_video", user["id"], pid, {"source_id": sid}, message=f"Importando {label}")
        created.append({"source_id": sid, "job": job, "label": label})
    return {"created": created}


@router.put("/projects/{pid}/sources/{sid}/provenance")
def set_provenance(pid: str, sid: str, body: dict[str, Any], user=Depends(current_user)) -> dict[str, Any]:
    services.load_project(pid, user["id"])
    s = services.load_source(sid, user["id"], pid)
    if body.get("evidence_asset_id"):
        storage.get_owned(body["evidence_asset_id"], user["id"])
    clean = policy.validate_provenance(body, s["origin"] if s["kind"] == "internet" else None)
    services.update_source(sid, provenance=clean)
    db.audit("declaracion_procedencia", user["id"], pid, source=sid, **clean)
    return clean


@router.post("/projects/{pid}/evidence")
def upload_evidence(pid: str, file: UploadFile = File(...), user=Depends(current_user)) -> dict[str, Any]:
    """Documento de licencia o autorización (PDF o imagen)."""
    services.load_project(pid, user["id"])
    ext = Path(file.filename or "").suffix.lower()
    if ext not in {".pdf", ".jpg", ".jpeg", ".png", ".heic"}:
        raise AppError("Formato no admitido para el documento (PDF, JPG, PNG o HEIC).")
    aid, dest = storage.new_path(user["id"], ext, project_id=pid)
    storage.save_stream(file.file, dest, max_bytes=20 * 1024 * 1024)
    a = storage.register(aid=aid, user_id=user["id"], project_id=pid, kind="evidence", path=dest, mime=file.content_type)
    return {"asset_id": a["id"], "sha256": a["sha256"]}


class SourcePatch(BaseModel):
    label: str


@router.patch("/projects/{pid}/sources/{sid}")
def rename_source(pid: str, sid: str, body: SourcePatch, user=Depends(current_user)) -> dict[str, Any]:
    services.load_source(sid, user["id"], pid)
    services.update_source(sid, label=body.label.strip()[:120] or "Vídeo")
    return source_out(services.load_source(sid, user["id"], pid))


@router.delete("/projects/{pid}/sources/{sid}")
def delete_source(pid: str, sid: str, user=Depends(current_user)) -> dict[str, bool]:
    p = services.load_project(pid, user["id"])
    s = services.load_source(sid, user["id"], pid)
    for j in db.all_("SELECT id, params FROM jobs WHERE project_id=? AND status IN ('queued','running')", (pid,)):
        if db.jload(j["params"], {}).get("source_id") == sid:
            queue.request_cancel(j["id"])
    ids = [s.get(k) for k in ("original_asset_id", "proxy_asset_id", "review_asset_id", "sprite_asset_id")]
    ids += [h["asset_id"] for h in s["video_history"]]
    for aid in filter(None, set(ids)):
        storage.delete_asset(aid)
    for c in db.all_("SELECT thumb_asset_id FROM characters WHERE source_id=?", (sid,)):
        if c["thumb_asset_id"]:
            storage.delete_asset(c["thumb_asset_id"])
    db.execute("DELETE FROM sources WHERE id=?", (sid,))
    tl = timeline.remove_source(p["timeline"], sid)
    services.update_project(pid, timeline=tl, timeline_version=p["timeline_version"] + 1)
    db.audit("fuente_eliminada", user["id"], pid, source=sid)
    return {"ok": True}


@router.get("/projects/{pid}/sources/{sid}/analysis")
def source_analysis(pid: str, sid: str, user=Depends(current_user)) -> dict[str, Any]:
    s = services.load_source(sid, user["id"], pid)
    interval = (s["analysis"] or {}).get("interval", 0.5)
    chars = db.all_("SELECT id,label,stats,thumb_asset_id,identity_id,track FROM characters WHERE source_id=?", (sid,))
    for c in chars:
        c["stats"] = db.jload(c["stats"], {})
        c["presence"] = _presence(db.jload(c.pop("track"), []), interval)
        c["source_id"] = sid
    return {"source": source_out(s), "analysis": s["analysis"], "characters": chars}


@router.get("/projects/{pid}/characters")
def project_characters(pid: str, user=Depends(current_user)) -> list[dict[str, Any]]:
    services.load_project(pid, user["id"])
    rows = db.all_("""SELECT c.id, c.label, c.stats, c.thumb_asset_id, c.identity_id, c.track, c.source_id,
                      s.label AS source_label, s.analysis FROM characters c JOIN sources s ON s.id=c.source_id
                      WHERE c.project_id=? ORDER BY s.created_at, c.label""", (pid,))
    for c in rows:
        c["stats"] = db.jload(c["stats"], {})
        c["presence"] = _presence(db.jload(c.pop("track"), []), (db.jload(c.pop("analysis"), {}) or {}).get("interval", 0.5))
    return rows


def _presence(track: list[dict], interval: float) -> list[dict[str, float]]:
    ranges: list[dict[str, float]] = []
    for d in track:
        if ranges and d["t"] - ranges[-1]["end"] <= interval * 2.5:
            ranges[-1]["end"] = d["t"]
        else:
            ranges.append({"start": d["t"], "end": d["t"]})
    return ranges


class CharacterPatch(BaseModel):
    label: str | None = None
    identity_id: str | None = None
    clear_identity: bool = False


@router.patch("/projects/{pid}/characters/{cid}")
def patch_character(pid: str, cid: str, body: CharacterPatch, user=Depends(current_user)) -> dict[str, Any]:
    services.load_project(pid, user["id"])
    c = db.one("SELECT * FROM characters WHERE id=? AND project_id=?", (cid, pid))
    if not c:
        raise NotFound("Personaje no encontrado.")
    if body.label is not None:
        db.execute("UPDATE characters SET label=? WHERE id=?", (body.label.strip()[:40] or c["label"], cid))
    if body.clear_identity:
        db.execute("UPDATE characters SET identity_id=NULL WHERE id=?", (cid,))
    elif body.identity_id:
        ident = services.load_identity(body.identity_id, user["id"])
        policy.require_identity_usable(ident, "imagen")
        db.execute("UPDATE characters SET identity_id=? WHERE id=?", (ident["id"], cid))
        db.audit("identidad_asignada", user["id"], pid, character=cid, identity=ident["id"])
    row = db.one("SELECT id,label,identity_id,thumb_asset_id,stats,source_id FROM characters WHERE id=?", (cid,))
    row["stats"] = db.jload(row["stats"], {})
    return row


@router.post("/projects/{pid}/sources/{sid}/history/{index}/restore")
def restore_version(pid: str, sid: str, index: int, user=Depends(current_user)) -> dict[str, Any]:
    """Vuelve a una versión anterior de la fuente procesada (index=-1: original)."""
    s = services.load_source(sid, user["id"], pid)
    hist = s["video_history"]
    if index == -1:
        target = s["proxy_asset_id"]
    elif 0 <= index < len(hist):
        target = hist[index]["asset_id"]
    else:
        raise NotFound("Versión no encontrada.")
    services.update_source(sid, working_asset_id=target)
    job = queue.enqueue("review", user["id"], pid, {"source_id": sid}, message="Generando copia de revisión")
    return {"ok": True, "job": job}


# ------------------------------------------------------------ Internet y vídeos

@router.get("/internet/connectors")
def connectors(user=Depends(current_user)) -> list[dict[str, Any]]:
    return [c.info() for c in registry.enabled()]


@router.get("/internet/search")
def search(connector: str, q: str, page: int = 1, open_only: bool = True, user=Depends(current_user)) -> dict[str, Any]:
    q = q.strip()
    if not q or len(q) > 200:
        raise AppError("Escriba entre 1 y 200 caracteres.")
    conn = registry.get(connector)
    if not conn.searchable:
        raise AppError(f"{conn.label} no admite búsqueda: pegue la URL del vídeo.", code="sin_busqueda")
    st = conn.status()
    if not st.get("available", True) or st.get("search") is False:
        raise AppError(st.get("reason") or "Búsqueda no disponible.", code="proveedor_no_disponible", status=503)
    results = conn.search(q, max(1, min(page, 20)), open_only)
    return {"connector": conn.name, "results": [r.as_dict() for r in results]}


class ResolveIn(BaseModel):
    url: str


@router.post("/internet/resolve")
def resolve(body: ResolveIn, user=Depends(current_user)) -> dict[str, Any]:
    return registry.resolve(body.url).as_dict()


class InternetImport(BaseModel):
    url: str
    provenance: dict[str, Any]
    start: float | None = None
    end: float | None = None
    label: str | None = None


@router.post("/projects/{pid}/sources/internet")
def import_from_internet(pid: str, body: InternetImport, user=Depends(current_user)) -> dict[str, Any]:
    project = services.load_project(pid, user["id"])
    services.require_declaration(project)
    cand = registry.resolve(body.url)  # se vuelve a resolver en el servidor: no se confía en el cliente
    if not cand.importable or not cand.file_url:
        raise PolicyError(cand.reason or "Esta fuente no permite importar el vídeo.", code="no_importable",
                          details={"alternatives": cand.alternatives})
    origin = cand.as_dict() | {"connector": cand.connector}
    prov = policy.validate_provenance(body.provenance, origin)
    label = (body.label or cand.title or "Vídeo de Internet")[:120]
    sid = _new_source(pid, user["id"], "internet", label, origin, prov, _fragment(body.start, body.end), None)
    db.audit("declaracion_procedencia", user["id"], pid, source=sid, url=cand.page_url, **prov)
    job = queue.enqueue("import_video", user["id"], pid, {"source_id": sid, "file_url": cand.file_url},
                        message=f"Importando desde {cand.platform}")
    return {"source_id": sid, "job": job, "candidate": cand.as_dict()}


@router.get("/internet/history")
def import_history(user=Depends(current_user)) -> list[dict[str, Any]]:
    rows = db.all_("""SELECT s.id, s.project_id, s.label, s.status, s.origin, s.created_at, p.name AS project
                      FROM sources s JOIN projects p ON p.id=s.project_id
                      WHERE s.user_id=? AND s.kind='internet' ORDER BY s.created_at DESC LIMIT 100""", (user["id"],))
    out = []
    for r in rows:
        o = db.jload(r.pop("origin"), {})
        r.update({"platform": o.get("platform"), "page_url": o.get("page_url"), "author": o.get("author"),
                  "license": (o.get("license") or {}).get("name")})
        out.append(r)
    return out
