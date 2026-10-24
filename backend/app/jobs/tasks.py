"""Implementación de los trabajos en segundo plano."""
from __future__ import annotations

import math
import shutil
from pathlib import Path
from typing import Any

from .. import db, services, storage
from ..ai import asr, face_detect, face_swap, lipsync, quality, tts
from ..config import settings
from ..errors import AppError, LimitExceeded, QualityError
from ..media import ffmpeg, provenance, render, subtitles, timeline
from ..policy import require_identity_usable
from ..security import sign_asset
from .queue import JobContext, enqueue, handler


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


def _review(ctx: JobContext, project: dict[str, Any]) -> str:
    """Genera la copia de revisión (480p, con marca de agua) del vídeo procesado actual."""
    wd = _workdir(ctx)
    out = wd / "review.mp4"
    render.burn_watermark(services.working_path(project), out, services.watermark_cfg(project), wd,
                          max_height=480, should_cancel=ctx.cancelled, on_progress=ctx.progress)
    old = project.get("review_asset_id")
    a = _new_asset(ctx, out, "review", ".mp4", meta={"working_asset_id": project["working_asset_id"]})
    services.update_project(project["id"], review_asset_id=a["id"])
    if old:
        storage.delete_asset(old)
    return a["id"]


# ------------------------------------------------------------------ importación

@handler("import_video")
def import_video(ctx: JobContext) -> dict[str, Any]:
    project = services.load_project(ctx.project_id, ctx.user_id)
    src = storage.get_owned(ctx.params["asset_id"], ctx.user_id)
    info = ffmpeg.probe(src["path"])
    if not info.has_video:
        raise AppError("El archivo no contiene vídeo.", code="sin_video")
    if info.duration > settings.max_video_seconds:
        raise LimitExceeded(f"El vídeo dura {info.duration:.0f} s y el máximo permitido es "
                            f"{settings.max_video_seconds} s. Recórtelo antes de importarlo.")
    wd = _workdir(ctx)
    out = wd / "proxy.mp4"
    ctx.phase(0, 0.9, "Convirtiendo el vídeo a un formato de trabajo")
    max_h = None if settings.allow_4k else 1080
    norm = ffmpeg.normalize_video(Path(src["path"]), out, max_height=max_h, on_progress=ctx.progress,
                                  should_cancel=ctx.cancelled)
    proxy = _new_asset(ctx, out, "source_proxy", ".mp4",
                       meta={"original_asset_id": src["id"], "fps": norm.fps,
                             "original": {"codec": info.vcodec, "width": info.width, "height": info.height,
                                          "fps": info.fps, "rotation": info.rotation}})
    tl = timeline.empty(norm.duration)
    services.update_project(project["id"], proxy_asset_id=proxy["id"], working_asset_id=proxy["id"],
                            source_asset_id=src["id"], timeline=tl,
                            timeline_version=project["timeline_version"] + 1, video_history=[], analysis=None,
                            review_asset_id=None)
    db.execute("DELETE FROM characters WHERE project_id=?", (project["id"],))
    ctx.phase(0.9, 1.0, "Preparando el análisis")
    analysis = enqueue("analyze", ctx.user_id, project["id"], {}, message="Analizando rostros y audio")
    _cleanup(ctx)
    return {"proxy_asset_id": proxy["id"], "duration": norm.duration, "width": norm.width,
            "height": norm.height, "fps": norm.fps, "analysis_job_id": analysis["id"]}


@handler("analyze")
def analyze(ctx: JobContext) -> dict[str, Any]:
    project = services.load_project(ctx.project_id, ctx.user_id)
    proxy = storage.get(project["proxy_asset_id"])
    path = Path(proxy["path"])
    ctx.phase(0, 0.75, "Detectando y siguiendo rostros")
    faces = face_detect.analyze_video(path, interval=0.5, on_progress=ctx.progress, should_cancel=ctx.cancelled)
    ctx.phase(0.75, 0.85, "Detectando actividad de audio")
    speech = ffmpeg.detect_speech_segments(path, proxy["duration"] or faces["duration"])
    ctx.phase(0.85, 0.95, "Detectando cambios de escena")
    scenes = ffmpeg.detect_scenes(path)
    ctx.phase(0.95, 1.0, "Generando miniaturas")
    db.execute("DELETE FROM characters WHERE project_id=?", (project["id"],))
    wd = _workdir(ctx)
    characters = []
    for i, track in enumerate(faces["tracks"][:12], 1):
        best = max(track, key=lambda d: d["w"] * d["h"])
        thumb_src = wd / f"t{i}.jpg"
        _face_thumb(path, best, thumb_src)
        thumb = _new_asset(ctx, thumb_src, "thumb", ".jpg")
        cid = db.new_id()
        stats = face_detect.track_stats(track, faces["interval"])
        db.execute("""INSERT INTO characters(id,project_id,label,track,stats,thumb_asset_id,created_at)
                      VALUES (?,?,?,?,?,?,?)""",
                   (cid, project["id"], f"Personaje {i}", db.jdump(track), db.jdump(stats), thumb["id"], db.now()))
        characters.append(cid)
    analysis = {"detector": faces["detector"], "interval": faces["interval"], "speech_segments": speech,
                "scenes": scenes, "characters": len(characters)}
    services.update_project(project["id"], analysis=analysis)
    _cleanup(ctx)
    return analysis


def _face_thumb(video: Path, det: dict, out: Path) -> None:
    pad = 0.35
    x = max(0, int(det["x"] - det["w"] * pad))
    y = max(0, int(det["y"] - det["h"] * pad))
    w = int(det["w"] * (1 + 2 * pad))
    h = int(det["h"] * (1 + 2 * pad))
    ffmpeg.run([settings.ffmpeg, "-y", "-ss", f"{det['t']:.3f}", "-i", str(video), "-frames:v", "1",
                "-vf", f"crop='min({w},iw-{x})':'min({h},ih-{y})':{x}:{y},scale=160:-2", "-q:v", "4", str(out)])


# ------------------------------------------------------------------ sustitución facial

def _character(project_id: str, cid: str) -> dict[str, Any]:
    c = db.one("SELECT * FROM characters WHERE id=? AND project_id=?", (cid, project_id))
    if not c:
        raise AppError("Personaje no encontrado.", code="personaje")
    c["track"] = db.jload(c["track"], [])
    c["stats"] = db.jload(c["stats"], {})
    return c


@handler("faceswap")
def faceswap(ctx: JobContext) -> dict[str, Any]:
    project = services.load_project(ctx.project_id, ctx.user_id)
    services.require_ready(project)
    char = _character(project["id"], ctx.params["character_id"])
    if not char["identity_id"]:
        raise AppError("Asigne primero una identidad autorizada al personaje.", code="sin_identidad")
    identity = services.load_identity(char["identity_id"], ctx.user_id)
    require_identity_usable(identity, "imagen")
    provider = face_swap.get_provider()
    ctx.phase(0, 0.05, "Preparando la identidad")
    ident = provider.prepare_identity(services.identity_photos(identity["id"]))
    base = services.working_path(project)
    info = ffmpeg.probe(base)
    fps = info.fps or 30
    wd = _workdir(ctx)
    preview = ctx.params.get("preview")
    if preview:
        start = max(0.0, float(preview["start"]))
        end = min(info.duration, start + min(6.0, float(preview.get("duration", 3.0))))
        ctx.phase(0.05, 0.85, "Generando vista previa del rostro")
        seg = wd / "prev_seg.mp4"
        provider.swap_video(base, seg, ident, char["track"], start=start, end=end, fps=fps,
                            on_progress=ctx.progress, should_cancel=ctx.cancelled)
        audio = wd / "prev.wav"
        ffmpeg.extract_audio_wav(base, audio, start=start, duration=end - start)
        muxed = wd / "prev_mux.mp4"
        services.mux_audio(seg, audio, muxed)
        out = wd / "preview.mp4"
        ctx.phase(0.85, 1.0, "Añadiendo marca de agua")
        render.burn_watermark(muxed, out, services.watermark_cfg(project), wd, max_height=720)
        a = _new_asset(ctx, out, "preview", ".mp4", meta={"type": "faceswap", "start": start,
                                                           "identities": [identity["id"]]})
        _cleanup(ctx)
        return {"preview_asset_id": a["id"], "start": start, "end": end}

    first = max(0.0, char["stats"]["first"] - 0.5)
    last = min(info.duration, char["stats"]["last"] + 0.5)
    n_chunks = max(1, math.ceil((last - first) / settings.chunk_seconds))
    done: list[str] = ctx.checkpoint.setdefault("chunks", [])
    totals = ctx.checkpoint.setdefault("totals", {"swapped": 0, "missed": 0, "frames": 0})
    for k in range(n_chunks):
        chunk_path = wd / f"chunk_{k:04d}.mp4"
        if str(k) in done and chunk_path.exists():
            continue
        s = first + k * settings.chunk_seconds
        e = min(last, s + settings.chunk_seconds)
        ctx.phase(0.05 + 0.8 * k / n_chunks, 0.05 + 0.8 * (k + 1) / n_chunks,
                  f"Sustituyendo rostro: fragmento {k + 1} de {n_chunks}")
        r = provider.swap_video(base, chunk_path, ident, char["track"], start=s, end=e, fps=fps,
                                on_progress=ctx.progress, should_cancel=ctx.cancelled)
        for key in totals:
            totals[key] += r.get(key, 0)
        done.append(str(k))
        ctx.save_checkpoint()
    ctx.phase(0.85, 0.92, "Uniendo fragmentos")
    listing = wd / "chunks.txt"
    listing.write_text("".join(f"file '{wd / f'chunk_{k:04d}.mp4'}'\n" for k in range(n_chunks)))
    joined = wd / "joined.mp4"
    ffmpeg.run([settings.ffmpeg, "-y", "-f", "concat", "-safe", "0", "-i", str(listing), "-c", "copy", str(joined)])
    out = wd / "swapped.mp4"
    services.splice(base, joined, first, last, out)
    tracked = totals["swapped"] + totals["missed"]
    ratio = totals["swapped"] / tracked if tracked else 0
    if tracked and ratio < 0.5:
        raise QualityError(f"Sólo se pudo sustituir el rostro en el {ratio:.0%} de los fotogramas en los que "
                           "aparece. Pruebe con fotografías de referencia más parecidas en ángulo e "
                           "iluminación o con un vídeo de mayor calidad.")
    new = _new_asset(ctx, out, "working", ".mp4", meta={"operation": "faceswap", "character_id": char["id"]})
    services.set_working(project, new["id"], "faceswap", [identity["id"]])
    ctx.phase(0.92, 1.0, "Generando copia de revisión")
    review_id = _review(ctx, project)
    db.audit("faceswap", ctx.user_id, project["id"], character=char["id"], identity=identity["id"],
             provider=provider.name)
    _cleanup(ctx)
    warnings = []
    if tracked and ratio < 0.9:
        warnings.append(f"El rostro no se sustituyó en el {1 - ratio:.0%} de los fotogramas (cara no detectada).")
    return {"working_asset_id": new["id"], "review_asset_id": review_id, "frames_swapped": totals["swapped"],
            "frames_missed": totals["missed"], "warnings": warnings}


# ------------------------------------------------------------------ diálogos

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
    c = tts.cost(voice, d["text"])
    if c:
        db.execute("INSERT INTO usage(user_id,provider,units,unit,cost,job_id,created_at) VALUES (?,?,?,?,?,?,?)",
                   (ctx.user_id, voice.provider, len(d["text"]), "caracteres", c, ctx.id, db.now()))
    _cleanup(ctx)
    return {"audio_asset_id": a["id"], "duration": finfo.duration, "segments": segments, "subtitles": subs,
            "warnings": warnings, "cost_usd": c}


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
    _cleanup(ctx)
    return {"audio_asset_id": a["id"], "duration": processed.duration, "subtitles": subs,
            "subtitle_source": source, "voiced_segments": voiced}


# ------------------------------------------------------------------ sincronización labial

def lipsync_check(project: dict[str, Any], character_id: str, start: float, duration: float) -> dict[str, Any]:
    char = _character(project["id"], character_id)
    others = [db.jload(r["track"], []) for r in
              db.all_("SELECT track FROM characters WHERE project_id=? AND id<>?", (project["id"], character_id))]
    interval = (project.get("analysis") or {}).get("interval", 0.5)
    rep = quality.assess_segment(char["track"], others, start, start + duration, interval)
    total = services.source_duration(project)
    if start + duration > total + 0.05:
        rep["blocking"].append(f"El diálogo ({duration:.1f} s) no cabe: a partir de {start:.1f} s sólo quedan "
                               f"{max(0.0, total - start):.1f} s de vídeo.")
        rep["ok"] = False
    rep["estimate"] = lipsync.estimate(duration)
    return rep


def _publisher(ctx: JobContext):
    def publish(path: Path) -> str:
        a = _new_asset(ctx, _copy(path), "segment", path.suffix,
                       meta={"temporary": True})
        db.execute("UPDATE assets SET expires_at=? WHERE id=?", (db.now() + 6 * 3600, a["id"]))
        token = sign_asset(a["id"], ttl=6 * 3600, purpose="provider")
        return f"{settings.public_base_url}/api/files/{token}"
    return publish


def _copy(path: Path) -> Path:
    dst = path.with_name(f"pub_{db.new_id()}{path.suffix}")
    shutil.copy(path, dst)
    return dst


@handler("lipsync")
def lipsync_job(ctx: JobContext) -> dict[str, Any]:
    project = services.load_project(ctx.project_id, ctx.user_id)
    services.require_ready(project)
    d = _dialogue(project["id"], ctx.params["dialogue_id"])
    if not d["audio_asset_id"]:
        raise AppError("El diálogo todavía no tiene audio.", code="dialogo_sin_audio")
    char = _character(project["id"], ctx.params["character_id"])
    identities = []
    if char["identity_id"]:
        ident = services.load_identity(char["identity_id"], ctx.user_id)
        require_identity_usable(ident, "imagen")
        identities.append(ident["id"])
    vid = d["settings"].get("voice_identity_id")
    if vid:
        require_identity_usable(services.load_identity(vid, ctx.user_id), "voz")
        identities.append(vid)
    start = float(ctx.params["source_start"])
    audio_path = Path(storage.get(d["audio_asset_id"])["path"])
    duration = float(d["duration"])
    preview = bool(ctx.params.get("preview"))
    if preview:
        duration = min(duration, 4.0)
    rep = lipsync_check(project, char["id"], start, duration)
    if rep["blocking"]:
        raise QualityError("El material no permite una sincronización labial aceptable: "
                           + " ".join(rep["blocking"]), details={"report": rep})
    if rep["warnings"] and not ctx.params.get("accept_warnings"):
        raise QualityError("Hay advertencias de calidad que debe revisar y aceptar antes de continuar.",
                           details={"report": rep})
    provider = lipsync.get_provider()
    base = services.working_path(project)
    wd = _workdir(ctx)
    seg = wd / "segment.mp4"
    seg_audio = wd / "segment_audio.wav"
    out = wd / "synced.mp4"
    if not out.exists():
        ctx.phase(0, 0.1, "Preparando el fragmento")
        ffmpeg.cut_segment(base, seg, start, duration, with_audio=True)
        ffmpeg.extract_audio_wav(audio_path, seg_audio, start=0, duration=duration)
        ctx.phase(0.1, 0.8, f"Sincronizando labios ({provider.name})")
        provider.run(seg, seg_audio, out, publish=_publisher(ctx), on_progress=ctx.progress,
                     should_cancel=ctx.cancelled, checkpoint=ctx.checkpoint)
        ctx.save_checkpoint()
        if provider.paid:
            cost = round(duration * settings.sync_cost_per_second, 3)
            db.execute("INSERT INTO usage(user_id,provider,units,unit,cost,job_id,created_at) VALUES (?,?,?,?,?,?,?)",
                       (ctx.user_id, provider.name, duration, "segundos", cost, ctx.id, db.now()))
    ctx.phase(0.8, 0.85, "Comprobando el resultado")
    post = _post_check(out, rep["metrics"].get("coverage", 1.0), duration)
    if preview:
        muxed = wd / "prev_mux.mp4"
        services.mux_audio(out, seg_audio, muxed)
        prev = wd / "preview.mp4"
        ctx.phase(0.85, 1.0, "Añadiendo marca de agua")
        render.burn_watermark(muxed, prev, services.watermark_cfg(project), wd, max_height=720)
        a = _new_asset(ctx, prev, "preview", ".mp4", meta={"type": "lipsync", "identities": identities})
        _cleanup(ctx)
        return {"preview_asset_id": a["id"], "quality": rep, "post_check": post}
    ctx.phase(0.85, 0.9, "Integrando el fragmento en el vídeo")
    spliced = wd / "spliced.mp4"
    services.splice(base, out, start, start + duration, spliced)
    new = _new_asset(ctx, spliced, "working", ".mp4", meta={"operation": "lipsync", "dialogue_id": d["id"]})
    services.set_working(project, new["id"], "lipsync", identities)
    _attach_dialogue(project, d, start, duration, ctx.params.get("replace_audio", True))
    ctx.phase(0.9, 1.0, "Generando copia de revisión")
    review_id = _review(ctx, project)
    db.audit("lipsync", ctx.user_id, project["id"], dialogue=d["id"], character=char["id"],
             provider=provider.name, seconds=duration)
    _cleanup(ctx)
    return {"working_asset_id": new["id"], "review_asset_id": review_id, "quality": rep, "post_check": post}


def _post_check(video: Path, pre_coverage: float, duration: float) -> dict[str, Any]:
    info = ffmpeg.probe(video)
    issues = []
    if abs(info.duration - duration) > 0.5:
        issues.append(f"La duración devuelta ({info.duration:.1f} s) no coincide con la del diálogo "
                      f"({duration:.1f} s).")
    res = face_detect.analyze_video(video, interval=0.5)
    best = max((len(t) for t in res["tracks"]), default=0)
    coverage = min(1.0, best / max(1, res["samples"]))
    if coverage < pre_coverage - 0.3:
        issues.append("En el resultado el rostro se detecta con menos frecuencia que en el original: "
                      "revise posibles deformaciones antes de exportar.")
    return {"ok": not issues, "issues": issues, "coverage": round(coverage, 2)}


def _attach_dialogue(project: dict[str, Any], d: dict[str, Any], start: float, duration: float,
                     replace_audio: bool) -> None:
    p = services.load_project(project["id"], project["user_id"])
    tl = p["timeline"]
    tl["dialogue"]["items"] = [it for it in tl["dialogue"]["items"] if it.get("dialogue_id") != d["id"]]
    tl["dialogue"]["items"].append({"id": d["id"][:10], "dialogue_id": d["id"], "asset_id": d["audio_asset_id"],
                                    "source_start": round(start, 3), "volume": 1.0})
    if replace_audio:
        tl["source_audio"]["mutes"].append({"start": round(start, 3), "end": round(start + duration, 3)})
    tl["subtitles"]["items"] = [s for s in tl["subtitles"]["items"] if s.get("dialogue_id") != d["id"]]
    for s in d["subtitles"]:
        tl["subtitles"]["items"].append({"id": db.new_id()[:10], "start": round(start + s["start"], 3),
                                         "end": round(start + s["end"], 3), "text": s["text"], "base": "source",
                                         "dialogue_id": d["id"]})
    services.update_project(p["id"], timeline=tl, timeline_version=p["timeline_version"] + 1)


# ------------------------------------------------------------------ revisión y exportación

@handler("review")
def review_job(ctx: JobContext) -> dict[str, Any]:
    project = services.load_project(ctx.project_id, ctx.user_id)
    rid = _review(ctx, project)
    _cleanup(ctx)
    return {"review_asset_id": rid}


@handler("render")
def render_job(ctx: JobContext) -> dict[str, Any]:
    project = services.load_project(ctx.project_id, ctx.user_id)
    services.require_ready(project)
    services.check_identities_still_valid(project, ctx.user_id)
    p = ctx.params
    opts = render.RenderOptions(resolution=p["resolution"], fps=int(p["fps"]), codec=p["codec"],
                                start=p.get("start"), end=p.get("end"))
    is_preview = p.get("kind") == "preview"
    render.validate_options(opts, preview=is_preview)
    tl = project["timeline"]
    wm = services.watermark_cfg(project)
    wd = _workdir(ctx)
    out = wd / "render.mp4"
    ops = services.operations(project)
    meta = provenance.mp4_metadata(project["name"], ops)
    ctx.phase(0, 0.9 if not is_preview else 1.0, "Renderizando")
    info = render.render(main_video=services.working_path(project), tl=tl, aspect=project["aspect"], opts=opts,
                         asset_paths=services.asset_paths_for_timeline(tl, ctx.user_id), wm_cfg=wm, workdir=wd,
                         output=out, metadata=meta, on_progress=ctx.progress, should_cancel=ctx.cancelled)
    identities = sorted(services.used_identities(project))
    if is_preview:
        a = _new_asset(ctx, out, "preview", ".mp4", meta={"type": "timeline", "identities": identities,
                                                           "start": opts.start, "end": opts.end})
        _cleanup(ctx)
        return {"preview_asset_id": a["id"]}
    ctx.phase(0.9, 1.0, "Añadiendo metadatos de procedencia")
    c2pa = provenance.embed_c2pa(out, provenance.build_manifest(project, ops, services.APP_VERSION), wd)
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
    side = provenance.write_sidecar(wd / "procedencia.json", project, ops, services.APP_VERSION,
                                    {"sha256_video": a["sha256"], "marca_de_agua": wm, "c2pa": c2pa,
                                     "procedencia_material": {k: project["provenance"].get(k) for k in
                                                              ("source_type", "rights_holder", "license_url")},
                                     "finalidad": project["use_declaration"]["purpose"]})
    result["manifest_asset_id"] = _new_asset(ctx, side, "manifest", ".json")["id"]
    db.audit("exportacion", ctx.user_id, project["id"], render=a["id"], sha256=a["sha256"],
             resolution=opts.resolution, codec=opts.codec, identities=identities, ack=p.get("ack"))
    _cleanup(ctx)
    return result
