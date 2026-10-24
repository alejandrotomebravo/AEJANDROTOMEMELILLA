"""Trabajos en segundo plano: retratos, guion, fotogramas, clips, voces y exportación."""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from .. import db, services, storage
from ..ai import asr, tts
from ..config import settings
from ..errors import AppError, ConsentError
from ..gen import images, llm, styles, video
from ..media import ffmpeg, provenance, render, subtitles, timeline
from ..policy import require_identity_usable
from .queue import JobContext, handler


def _workdir(ctx: JobContext) -> Path:
    """Directorio de trabajo estable por trabajo: permite reanudar tras un fallo."""
    d = settings.temp_dir / f"job_{ctx.id}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _cleanup(ctx: JobContext) -> None:
    shutil.rmtree(settings.temp_dir / f"job_{ctx.id}", ignore_errors=True)


def _new_asset(ctx: JobContext, src: Path, kind: str, ext: str, *, meta: dict | None = None,
               mime: str | None = None) -> dict[str, Any]:
    aid, dest = storage.new_path(ctx.user_id, ext, project_id=ctx.project_id)
    shutil.move(str(src), dest)
    info = None
    if ext in (".mp4", ".wav", ".mp3", ".m4a"):
        info = ffmpeg.probe(dest)
    return storage.register(
        aid=aid, user_id=ctx.user_id, project_id=ctx.project_id, kind=kind, path=dest,
        mime=mime or {".mp4": "video/mp4", ".wav": "audio/wav", ".jpg": "image/jpeg", ".srt": "text/plain",
                      ".json": "application/json"}.get(ext, "application/octet-stream"),
        duration=info.duration if info else None,
        width=info.width if info else None, height=info.height if info else None, meta=meta)


def _image_pair(ctx: JobContext, raw: Path, meta: dict) -> tuple[dict, dict]:
    """Guarda la imagen generada (uso interno) y su versión con aviso visible (la única que se sirve)."""
    wd = _workdir(ctx)
    prev = wd / f"prev_{raw.name}"
    images.watermark_image(raw, prev)
    a = _new_asset(ctx, raw, "generated_image", ".jpg", meta=meta, mime="image/jpeg")
    b = _new_asset(ctx, prev, "preview_image", ".jpg", meta=meta | {"of": a["id"]}, mime="image/jpeg")
    return a, b


# ------------------------------------------------------------------ personajes

@handler("portrait")
def portrait(ctx: JobContext) -> dict[str, Any]:
    project = services.load_project(ctx.project_id, ctx.user_id)
    c = services.load_cast(ctx.params["cast_id"], project["id"])
    db.execute("UPDATE cast_members SET status='generating', error=NULL WHERE id=?", (c["id"],))
    wd = _workdir(ctx)
    out = wd / "portrait.jpg"
    try:
        ctx.phase(0, 0.9, "Creando el retrato del personaje")
        if c["identity_id"]:
            ident = services.load_identity(c["identity_id"], ctx.user_id)
            require_identity_usable(ident, "imagen")
            info = images.portrait(services.identity_photos(ident["id"]), c["style"], c["description"], out,
                                   checkpoint=ctx.checkpoint, on_progress=ctx.progress, should_cancel=ctx.cancelled)
        else:
            info = images.fictional_portrait(c["name"], c["description"], c["style"], out, checkpoint=ctx.checkpoint,
                                             on_progress=ctx.progress, should_cancel=ctx.cancelled)
    except Exception as exc:
        db.execute("UPDATE cast_members SET status='failed', error=? WHERE id=?",
                   ((exc.message if isinstance(exc, AppError) else "Error al generar el retrato.")[:300], c["id"]))
        raise
    ctx.phase(0.9, 1.0, "Guardando")
    meta = {"cast_id": c["id"], "identities": [c["identity_id"]] if c["identity_id"] else [], **info}
    a, b = _image_pair(ctx, out, meta)
    for k in ("portrait_asset_id", "portrait_preview_asset_id"):
        if c.get(k):
            storage.delete_asset(c[k])
    db.execute("UPDATE cast_members SET portrait_asset_id=?, portrait_preview_asset_id=?, status='ready', meta=? WHERE id=?",
               (a["id"], b["id"], db.jdump({"provider": info["provider"], "model": info.get("model")}), c["id"]))
    db.audit("retrato_generado", ctx.user_id, project["id"], cast=c["id"], identity=c["identity_id"], provider=info["provider"])
    _cleanup(ctx)
    return {"portrait_preview_asset_id": b["id"], "provider": info["provider"]}


# ------------------------------------------------------------------ guion

@handler("storyboard")
def storyboard(ctx: JobContext) -> dict[str, Any]:
    project = services.load_project(ctx.project_id, ctx.user_id)
    cast = services.list_cast(project["id"])
    style = styles.get(services.project_style(project))
    ctx.progress(0.2, "Escribiendo el guion y los prompts")
    board = llm.storyboard(project["brief"], cast, style_label=style["label"], aspect=project["aspect"],
                           max_scenes=int(ctx.params.get("max_scenes") or settings.max_scenes),
                           target_seconds=ctx.params.get("target_seconds"))
    board = llm.normalize(board, cast, settings.max_scenes)
    old = services.scenes(project)
    services.update_project(project["id"], storyboard=board)
    for s in old:  # los fotogramas y clips del guion anterior ya no corresponden
        _discard_scene_media(project["id"], ctx.user_id, s)
    db.audit("guion_generado", ctx.user_id, project["id"], provider=board.get("provider"), scenes=len(board["scenes"]))
    return {"scenes": len(board["scenes"]), "provider": board.get("provider")}


@handler("rewrite_scene")
def rewrite_scene(ctx: JobContext) -> dict[str, Any]:
    project = services.load_project(ctx.project_id, ctx.user_id)
    cast = services.list_cast(project["id"])
    idx, scene = services.find_scene(project, ctx.params["scene_id"])
    style = styles.get(services.project_style(project))
    new = llm.rewrite_scene(project["storyboard"], idx, ctx.params.get("instruction", ""), cast,
                            style_label=style["label"], aspect=project["aspect"])
    new = llm.normalize({"scenes": [new]}, cast, 1)["scenes"][0]
    new.pop("id", None)
    services.save_scene(project["id"], ctx.user_id, scene["id"], **{k: v for k, v in new.items()
                                                                    if k not in ("keyframe", "clip")})
    return {"scene_id": scene["id"]}


def _discard_scene_media(project_id: str, user_id: str, scene: dict[str, Any]) -> None:
    for part in ("keyframe", "clip"):
        info = scene.get(part) or {}
        for k in ("asset_id", "preview_asset_id"):
            if info.get(k):
                storage.delete_asset(info[k])
        if part == "clip" and info.get("source_id"):
            services.remove_scene_clip(project_id, user_id, info["source_id"])
            services.delete_source(info["source_id"])


# ------------------------------------------------------------------ escenas

def _scene_cast(project: dict[str, Any], scene: dict[str, Any], user_id: str) -> list[dict[str, Any]]:
    services.require_cast_usable(project, user_id, scene.get("characters", []))
    cast = [c for c in services.list_cast(project["id"], with_paths=True) if c["name"] in scene.get("characters", [])]
    missing = [c["name"] for c in cast if c["status"] != "ready" or not c.get("portrait_path")]
    if missing:
        raise AppError(f"Genere primero el retrato de: {', '.join(missing)}.", code="retrato_pendiente")
    return cast


@handler("keyframe")
def keyframe(ctx: JobContext) -> dict[str, Any]:
    project = services.load_project(ctx.project_id, ctx.user_id)
    services.require_declaration(project)
    _, scene = services.find_scene(project, ctx.params["scene_id"])
    cast = _scene_cast(project, scene, ctx.user_id)
    wd = _workdir(ctx)
    out = wd / "keyframe.jpg"
    ctx.phase(0, 0.9, "Generando el fotograma clave")
    info = images.keyframe(scene, cast, services.project_style(project), project["aspect"], out,
                           checkpoint=ctx.checkpoint, on_progress=ctx.progress, should_cancel=ctx.cancelled)
    meta = {"scene_id": scene["id"], "identities": [c["identity_id"] for c in cast if c["identity_id"]], **info}
    a, b = _image_pair(ctx, out, meta)
    old = scene.get("keyframe") or {}
    for k in ("asset_id", "preview_asset_id"):
        if old.get(k):
            storage.delete_asset(old[k])
    services.save_scene(project["id"], ctx.user_id, scene["id"],
                        keyframe={"asset_id": a["id"], "preview_asset_id": b["id"], "provider": info["provider"],
                                  "prompt": info.get("prompt"), "at": db.now()})
    _cleanup(ctx)
    return {"preview_asset_id": b["id"], "provider": info["provider"]}


@handler("animate")
def animate(ctx: JobContext) -> dict[str, Any]:
    project = services.load_project(ctx.project_id, ctx.user_id)
    services.require_declaration(project)
    _, scene = services.find_scene(project, ctx.params["scene_id"])
    cast = _scene_cast(project, scene, ctx.user_id)
    kf = scene.get("keyframe") or {}
    if not kf.get("asset_id"):
        raise AppError("Genere primero el fotograma clave de la escena.", code="fotograma_pendiente")
    wd = _workdir(ctx)
    out = wd / "clip.mp4"
    if not out.exists():
        ctx.phase(0, 0.85, "Generando el clip de vídeo")
        info = video.animate(scene, Path(storage.get(kf["asset_id"])["path"]), services.project_style(project),
                             project["aspect"], out, checkpoint=ctx.checkpoint, on_progress=ctx.progress,
                             should_cancel=ctx.cancelled)
        ctx.checkpoint["info"] = info
        ctx.save_checkpoint()
    info = ctx.checkpoint["info"]
    ctx.phase(0.85, 0.95, "Preparando la copia de revisión")
    media = ffmpeg.probe(out)
    identities = [c["identity_id"] for c in cast if c["identity_id"]]
    clip = _new_asset(ctx, out, "working", ".mp4", meta={"scene_id": scene["id"], "identities": identities, **info})
    sid = db.new_id()
    db.execute("""INSERT INTO sources(id,project_id,user_id,kind,label,status,origin,proxy_asset_id,working_asset_id,
                  duration,width,height,fps,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
               (sid, project["id"], ctx.user_id, "generated", scene["title"], "ready",
                db.jdump({"scene_id": scene["id"], "identities": identities, **info}), clip["id"], clip["id"],
                media.duration, media.width, media.height, media.fps, db.now()))
    review = wd / "review.mp4"
    render.burn_watermark(Path(clip["path"]), review, services.watermark_cfg(project), wd, max_height=480)
    rv = _new_asset(ctx, review, "review", ".mp4", meta={"source_id": sid})
    db.execute("UPDATE sources SET review_asset_id=? WHERE id=?", (rv["id"], sid))
    old = scene.get("clip") or {}
    services.save_scene(project["id"], ctx.user_id, scene["id"],
                        clip={"source_id": sid, "preview_asset_id": rv["id"], "provider": info["provider"],
                              "seconds": info["seconds"], "prompt": info.get("prompt"), "at": db.now()})
    services.place_scene_clip(project["id"], ctx.user_id, scene["id"], sid, media.duration, old.get("source_id"))
    if old.get("source_id"):
        services.delete_source(old["source_id"])
    attach_scene_dialogues(project["id"], ctx.user_id, scene["id"])
    db.audit("clip_generado", ctx.user_id, project["id"], scene=scene["id"], provider=info["provider"],
             identities=identities)
    _cleanup(ctx)
    return {"preview_asset_id": rv["id"], "source_id": sid, "provider": info["provider"]}


def attach_scene_dialogues(project_id: str, user_id: str, scene_id: str | None) -> None:
    """Coloca las voces de una escena sobre su clip (en orden, con 0,3 s de separación) y genera
    sus subtítulos. Se llama al terminar una voz y al regenerar el clip."""
    if not scene_id:
        return
    with db.tx():
        p = services.load_project(project_id, user_id)
        try:
            _, scene = services.find_scene(p, scene_id)
        except Exception:
            return
        sid = (scene.get("clip") or {}).get("source_id")
        if not sid:
            return
        rows = [r for r in db.all_("SELECT * FROM dialogues WHERE project_id=? ORDER BY created_at", (project_id,))
                if db.jload(r["settings"], {}).get("scene_id") == scene_id and r["audio_asset_id"]]
        ids = {r["id"] for r in rows}
        tl = p["timeline"]
        tl["dialogue"]["items"] = [i for i in tl["dialogue"]["items"] if i.get("dialogue_id") not in ids]
        tl["subtitles"]["items"] = [s for s in tl["subtitles"]["items"] if s.get("dialogue_id") not in ids]
        t = 0.3
        for r in rows:
            tl["dialogue"]["items"].append({"id": r["id"][:10], "dialogue_id": r["id"], "asset_id": r["audio_asset_id"],
                                            "source_id": sid, "source_start": round(t, 3), "volume": 1.0})
            for s in db.jload(r["subtitles"], []):
                tl["subtitles"]["items"].append({"id": db.new_id()[:10], "start": round(t + s["start"], 3),
                                                 "end": round(t + s["end"], 3), "text": s["text"], "base": "source",
                                                 "source_id": sid, "dialogue_id": r["id"]})
            t += float(r["duration"] or 0) + 0.3
        db.execute("UPDATE projects SET timeline=?, timeline_version=timeline_version+1, updated_at=? WHERE id=?",
                   (db.jdump(tl), db.now(), project_id))


# ------------------------------------------------------------------ voces

def _dialogue(project_id: str, did: str) -> dict[str, Any]:
    d = db.one("SELECT * FROM dialogues WHERE id=? AND project_id=?", (did, project_id))
    if not d:
        raise AppError("Diálogo no encontrado.", code="dialogo")
    d["settings"] = db.jload(d["settings"], {})
    d["segments"] = db.jload(d["segments"], [])
    d["subtitles"] = db.jload(d["subtitles"], [])
    return d


@handler("tts")
def synthesize(ctx: JobContext) -> dict[str, Any]:
    project = services.load_project(ctx.project_id, ctx.user_id)
    d = _dialogue(project["id"], ctx.params["dialogue_id"])
    s = d["settings"]
    voice = tts.get_voice(s["voice_id"])
    rules = project["settings"].get("pronunciations", {})
    parts = tts.parse_script(d["text"])
    wd = _workdir(ctx)
    pieces: list[Path] = []
    segments = []
    t = 0.0
    speech_parts = [p for p in parts if p["type"] == "speech"]
    done = 0
    for i, p in enumerate(parts):
        ctx.check_cancel()
        piece = wd / f"p{i:03d}.wav"
        if p["type"] == "pause":
            ffmpeg.silence_wav(piece, p["seconds"])
            dur = p["seconds"]
        else:
            ctx.progress(done / max(1, len(speech_parts)), f"Sintetizando tramo {done + 1} de {len(speech_parts)}")
            spoken = tts.apply_pronunciations(p["text"], rules)
            dur = tts.synthesize_piece(voice, spoken, piece, speed=float(s.get("speed", 1.0)),
                                       pitch=float(s.get("pitch", 0.0)))
            segments.append({"start": round(t, 3), "end": round(t + dur, 3), "text": p["text"]})
            done += 1
        pieces.append(piece)
        t += dur
    joined = wd / "dialogue.wav"
    info = ffmpeg.concat_audio(pieces, joined)
    tempo = 1.0
    target = s.get("target_duration")
    warnings = []
    if target:
        tempo = info.duration / float(target)
        if tempo < 0.8 or tempo > 1.25:
            warnings.append(f"Para ajustarse a {float(target):.1f} s habría que cambiar el ritmo un "
                            f"{abs(tempo - 1):.0%}; se limita al 20-25 % para que la voz no suene artificial.")
            tempo = min(1.25, max(0.8, tempo))
    final = wd / "final.wav"
    finfo = ffmpeg.process_audio(joined, final, tempo=tempo, gain_db=float(s.get("gain_db", 0.0)))
    scale = finfo.duration / info.duration if info.duration else 1.0
    segments = [{"start": round(x["start"] * scale, 3), "end": round(x["end"] * scale, 3), "text": x["text"]}
                for x in segments]
    subs = []
    for seg in segments:
        subs += subtitles.time_blocks(subtitles.chunk_text(seg["text"]), seg["start"], seg["end"])
    a = _new_asset(ctx, final, "dialogue_audio", ".wav", meta={"dialogue_id": d["id"], "voice": voice.id})
    old = d.get("audio_asset_id")
    db.execute("UPDATE dialogues SET audio_asset_id=?, duration=?, segments=?, subtitles=? WHERE id=?",
               (a["id"], finfo.duration, db.jdump(segments), db.jdump(subs), d["id"]))
    if old:
        _release_old_audio(project, old)
    attach_scene_dialogues(project["id"], ctx.user_id, d["settings"].get("scene_id"))
    _cleanup(ctx)
    return {"audio_asset_id": a["id"], "duration": finfo.duration, "segments": segments, "subtitles": subs,
            "warnings": warnings}


def _release_old_audio(project: dict[str, Any], asset_id: str) -> None:
    if asset_id not in timeline.referenced_assets(project["timeline"]):
        storage.delete_asset(asset_id)


@handler("audio_import")
def audio_import(ctx: JobContext) -> dict[str, Any]:
    project = services.load_project(ctx.project_id, ctx.user_id)
    d = _dialogue(project["id"], ctx.params["dialogue_id"])
    raw = storage.get_owned(ctx.params["raw_asset_id"], ctx.user_id)
    wd = _workdir(ctx)
    wav = wd / "imported.wav"
    ctx.progress(0.1, "Convirtiendo el audio")
    info = ffmpeg.probe(raw["path"])
    if not info.has_audio:
        raise AppError("El archivo no contiene audio.", code="sin_audio")
    if info.duration > settings.max_dialogue_seconds:
        raise LimitExceeded(f"El audio supera el máximo de {settings.max_dialogue_seconds} s por diálogo.")
    s = d["settings"]
    processed = ffmpeg.process_audio(Path(raw["path"]), wav, tempo=float(s.get("speed", 1.0)),
                                     semitones=float(s.get("pitch", 0.0)), gain_db=float(s.get("gain_db", 0.0)))
    voiced = ffmpeg.detect_speech_segments(wav, processed.duration)
    subs: list[dict[str, Any]] = []
    source = "ninguno"
    if d["text"]:
        start = voiced[0]["start"] if voiced else 0.0
        end = voiced[-1]["end"] if voiced else processed.duration
        subs = subtitles.time_blocks(subtitles.chunk_text(tts.PAUSE_RE.sub(" ", d["text"])), start, end)
        source = "texto"
    elif asr.status()["available"]:
        ctx.progress(0.5, "Transcribiendo para generar subtítulos")
        for seg in asr.transcribe(wav, s.get("language", "es")[:2]):
            subs += subtitles.time_blocks(subtitles.chunk_text(seg["text"]), seg["start"], seg["end"])
        source = "reconocimiento_de_voz"
    a = _new_asset(ctx, wav, "dialogue_audio", ".wav", meta={"dialogue_id": d["id"], "mode": d["mode"]})
    old = d.get("audio_asset_id")
    db.execute("UPDATE dialogues SET audio_asset_id=?, duration=?, subtitles=? WHERE id=?",
               (a["id"], processed.duration, db.jdump(subs), d["id"]))
    if old:
        _release_old_audio(project, old)
    storage.delete_asset(raw["id"])
    attach_scene_dialogues(project["id"], ctx.user_id, d["settings"].get("scene_id"))
    _cleanup(ctx)
    return {"audio_asset_id": a["id"], "duration": processed.duration, "subtitles": subs,
            "subtitle_source": source, "voiced_segments": voiced}


# ------------------------------------------------------------------ sincronización labial


# ------------------------------------------------------------------ exportación

@handler("render")
def render_job(ctx: JobContext) -> dict[str, Any]:
    project = services.load_project(ctx.project_id, ctx.user_id)
    services.require_declaration(project)
    services.require_cast_usable(project, ctx.user_id)
    p = ctx.params
    opts = render.RenderOptions(resolution=p["resolution"], fps=int(p["fps"]), codec=p["codec"],
                                start=p.get("start"), end=p.get("end"))
    is_preview = p.get("kind") == "preview"
    render.validate_options(opts, preview=is_preview)
    tl = project["timeline"]
    wm = services.watermark_cfg(project)
    wd = _workdir(ctx)
    out = wd / "render.mp4"
    ops = ["generación de vídeo con IA"]
    meta = provenance.mp4_metadata(project["name"], ops)
    ctx.phase(0, 0.9 if not is_preview else 1.0, "Renderizando")
    info = render.render(sources=services.timeline_sources(project, ctx.user_id), tl=tl, aspect=project["aspect"],
                         opts=opts, asset_paths=services.asset_paths_for_timeline(tl, ctx.user_id), wm_cfg=wm,
                         workdir=wd, output=out, metadata=meta, on_progress=ctx.progress, should_cancel=ctx.cancelled)
    identities = sorted({c["identity_id"] for c in services.list_cast(project["id"]) if c["identity_id"]})
    if is_preview:
        a = _new_asset(ctx, out, "preview", ".mp4", meta={"type": "timeline", "identities": identities,
                                                           "start": opts.start, "end": opts.end})
        _cleanup(ctx)
        return {"preview_asset_id": a["id"]}
    ctx.phase(0.9, 1.0, "Añadiendo metadatos de procedencia")
    c2pa = provenance.embed_c2pa(out, provenance.build_manifest(project, ops, services.APP_VERSION, generated=True), wd)
    result: dict[str, Any] = {"c2pa": c2pa}
    a = _new_asset(ctx, out, "render", ".mp4",
                   meta={"identities": identities, "resolution": opts.resolution, "fps": opts.fps,
                         "codec": opts.codec, "watermark": wm, "operations": ops, "c2pa": c2pa})
    result["render_asset_id"] = a["id"]
    result["size_bytes"] = a["size"]
    result["duration"] = info.duration
    subs = timeline.output_subtitles(tl)
    if subs:
        srt = wd / "subtitulos.srt"
        srt.write_text(subtitles.to_srt(subs), encoding="utf-8")
        result["srt_asset_id"] = _new_asset(ctx, srt, "subtitles", ".srt")["id"]
    providers = sorted({(s.get("clip") or {}).get("provider", "") for s in services.scenes(project)} - {""})
    side = provenance.write_sidecar(wd / "procedencia.json", project, ops, services.APP_VERSION,
                                    {"sha256_video": a["sha256"], "marca_de_agua": wm, "c2pa": c2pa,
                                     "guion": {"proveedor": project["storyboard"].get("provider"),
                                               "modelo": project["storyboard"].get("model")},
                                     "proveedores_de_video": providers,
                                     "personajes": [{"nombre": c["name"], "persona_real": bool(c["identity_id"]),
                                                     "estilo": c["style"]} for c in services.list_cast(project["id"])],
                                     "finalidad": project["use_declaration"]["purpose"]})
    result["manifest_asset_id"] = _new_asset(ctx, side, "manifest", ".json")["id"]
    meta2 = a["meta"] | {"srt_asset_id": result.get("srt_asset_id"), "manifest_asset_id": result["manifest_asset_id"]}
    db.execute("UPDATE assets SET meta=? WHERE id=?", (db.jdump(meta2), a["id"]))
    db.audit("exportacion", ctx.user_id, project["id"], render=a["id"], sha256=a["sha256"],
             resolution=opts.resolution, codec=opts.codec, identities=identities, ack=p.get("ack"))
    _cleanup(ctx)
    return result
