"""Composición final: línea de tiempo → vídeo con marca de agua obligatoria."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from ..config import settings
from ..errors import AppError, LimitExceeded
from . import ffmpeg, subtitles, timeline, watermark

RESOLUTIONS = {"360p": 360, "720p": 720, "1080p": 1080, "2160p": 2160}
FPS_OPTIONS = {24, 25, 30, 60}
CODECS = {"h264": "libx264", "h265": "libx265"}
# Mbit/s aproximados de vídeo con CRF 20 (H.264) en contenido típico.
BITRATE_H264 = {"360p": 1.0, "720p": 4.5, "1080p": 8.0, "2160p": 30.0}


@dataclass
class RenderOptions:
    resolution: str = "1080p"
    fps: int = 30
    codec: str = "h264"
    crf: int | None = None
    start: float | None = None  # sólo vistas previas (tiempo de salida)
    end: float | None = None


def output_size(aspect: str, resolution: str) -> tuple[int, int]:
    short = RESOLUTIONS[resolution]
    if aspect == "16:9":
        return int(round(short * 16 / 9 / 2) * 2), short
    if aspect == "9:16":
        return short, int(round(short * 16 / 9 / 2) * 2)
    return short, short


def validate_options(opts: RenderOptions, source_info: dict[str, Any] | None = None,
                     preview: bool = False) -> RenderOptions:
    if opts.resolution not in RESOLUTIONS:
        raise AppError("Resolución no admitida.", code="exportacion")
    if opts.resolution == "2160p" and not settings.allow_4k:
        raise LimitExceeded("La exportación 4K está desactivada en este servidor (ALLOW_4K).")
    if opts.fps not in FPS_OPTIONS:
        raise AppError("Frecuencia de fotogramas no admitida (24, 25, 30 o 60).", code="exportacion")
    if opts.codec not in CODECS:
        raise AppError("Códec no admitido.", code="exportacion")
    if not ffmpeg.has_encoder(CODECS[opts.codec]):
        raise AppError(f"El códec {opts.codec.upper()} no está disponible en este FFmpeg.",
                       code="codec_no_disponible")
    if source_info and not preview:
        src_fps = source_info.get("fps") or 30
        if opts.fps > src_fps + 1:
            # No se inventan fotogramas: se duplican. Se permite, pero se informa.
            pass
    return opts


def estimate(tl: dict[str, Any], aspect: str, opts: RenderOptions) -> dict[str, Any]:
    dur = timeline.output_duration(tl)
    if opts.start is not None and opts.end is not None:
        dur = max(0.0, min(dur, opts.end) - opts.start)
    rate = BITRATE_H264[opts.resolution] * (0.6 if opts.codec == "h265" else 1.0)
    rate *= (opts.fps / 30) ** 0.5
    size_mb = dur * (rate + 0.192) / 8
    factor = settings.render_seconds_per_second.get(opts.resolution, 1.0)
    factor *= 2.2 if opts.codec == "h265" else 1.0
    factor *= opts.fps / 30
    w, h = output_size(aspect, opts.resolution)
    return {
        "duration_seconds": round(dur, 2),
        "width": w, "height": h,
        "estimated_size_mb": round(size_mb, 1),
        "estimated_render_seconds": round(max(3.0, dur * factor + 2), 0),
        "note": "Estimación orientativa en CPU; depende del contenido y del servidor.",
    }


def _fit_filter(fit: str, w: int, h: int, label_in: str, label_out: str, idx: int) -> str:
    if fit == "cover":
        return (f"[{label_in}]scale={w}:{h}:force_original_aspect_ratio=increase,"
                f"crop={w}:{h},setsar=1[{label_out}]")
    if fit == "contain":
        return (f"[{label_in}]scale={w}:{h}:force_original_aspect_ratio=decrease,"
                f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1[{label_out}]")
    return (f"[{label_in}]split=2[fg{idx}][bg{idx}];"
            f"[bg{idx}]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},boxblur=20:2[bgb{idx}];"
            f"[fg{idx}]scale={w}:{h}:force_original_aspect_ratio=decrease[fgs{idx}];"
            f"[bgb{idx}][fgs{idx}]overlay=(W-w)/2:(H-h)/2,setsar=1[{label_out}]")


def _atempo(speed: float) -> str:
    return ",".join(ffmpeg.atempo_chain(speed)) if abs(speed - 1.0) > 1e-3 else "anull"


def build_command(*, sources: dict[str, Path], tl: dict[str, Any], aspect: str, opts: RenderOptions,
                  asset_paths: dict[str, Path], wm_cfg: dict[str, Any], workdir: Path, output: Path,
                  metadata: dict[str, str] | None = None, credits: list[str] | None = None,
                  source_durations: dict[str, float] | None = None) -> tuple[list[str], float]:
    """Construye la orden de FFmpeg para una línea de tiempo con varias fuentes.

    ``sources``: id de fuente → vídeo de trabajo (siempre con pista de audio, la
    normalización la garantiza)."""
    clips = tl["video"]["clips"]
    if not clips:
        raise AppError("La línea de tiempo no contiene escenas de vídeo.", code="linea_de_tiempo")
    w, h = output_size(aspect, opts.resolution)
    used = timeline.used_sources(tl)
    durations = source_durations or {sid: ffmpeg.probe(sources[sid]).duration for sid in used}
    args = [settings.ffmpeg, "-y"]
    fc: list[str] = []
    n_inputs = 0
    src_idx: dict[str, int] = {}
    for sid in used:
        args += ["-i", str(sources[sid])]
        src_idx[sid] = n_inputs
        n_inputs += 1
    AF = "aformat=sample_rates=48000:channel_layouts=stereo"

    # --- Audio en tiempo de origen por fuente: original (+silencios) y diálogos ---
    per_source_clips = {sid: [i for i, c in enumerate(clips) if c["source_id"] == sid] for sid in used}
    for sid in used:
        i = src_idx[sid]
        chain = [AF, f"volume={tl['source_audio']['volume']}"]
        for m in tl["source_audio"]["mutes"]:
            if m["source_id"] == sid:
                chain.append(f"volume=enable='between(t,{m['start']},{m['end']})':volume=0")
        fc.append(f"[{i}:a]{','.join(chain)}[o_{i}]")
        dur = durations[sid]
        fc.append(f"anullsrc=channel_layout=stereo:sample_rate=48000,atrim=duration={dur:.3f}[z_{i}]")
        mix = [f"[z_{i}]"]
        for k, it in enumerate(it for it in tl["dialogue"]["items"] if it["source_id"] == sid):
            args += ["-i", str(asset_paths[it["asset_id"]])]
            j = n_inputs
            n_inputs += 1
            delay = int(round(it["source_start"] * 1000))
            fc.append(f"[{j}:a]{AF},volume={it['volume'] * tl['dialogue']['volume']},adelay={delay}|{delay}[d_{i}_{k}]")
            mix.append(f"[d_{i}_{k}]")
        if len(mix) > 1:
            fc.append(f"{''.join(mix)}amix=inputs={len(mix)}:normalize=0:duration=first[dl_{i}]")
        else:
            fc.append(f"[z_{i}]anull[dl_{i}]")
        n = len(per_source_clips[sid])
        fc.append(f"[{i}:v]split={n}" + "".join(f"[vs_{i}_{k}]" for k in range(n)))
        fc.append(f"[o_{i}]asplit={n}" + "".join(f"[os_{i}_{k}]" for k in range(n)))
        fc.append(f"[dl_{i}]asplit={n}" + "".join(f"[ds_{i}_{k}]" for k in range(n)))

    # --- Escenas: recorte, velocidad, encaje, color, fundidos ---
    concat_in = []
    seen: dict[str, int] = {}
    for ci, c in enumerate(clips):
        i = src_idx[c["source_id"]]
        k = seen.get(c["source_id"], 0)
        seen[c["source_id"]] = k + 1
        sp = c.get("speed", 1.0)
        length = timeline.clip_length(c)
        vf = [f"trim=start={c['in']}:end={c['out']}", "setpts=PTS-STARTPTS"]
        if abs(sp - 1) > 1e-3:
            vf.append(f"setpts=PTS/{sp}")
        fc.append(f"[vs_{i}_{k}]{','.join(vf)}[vt{ci}]")
        fc.append(_fit_filter(tl.get("fit", "contain_blur"), w, h, f"vt{ci}", f"vf{ci}", ci))
        post = [f"fps={opts.fps}"]
        col = c.get("color") or {}
        if col and (col.get("brightness", 0) or col.get("contrast", 1) != 1 or col.get("saturation", 1) != 1):
            post.append(f"eq=brightness={col.get('brightness', 0)}:contrast={col.get('contrast', 1)}:"
                        f"saturation={col.get('saturation', 1)}")
        if c.get("fade_in"):
            post.append(f"fade=t=in:st=0:d={c['fade_in']}")
        if c.get("fade_out"):
            post.append(f"fade=t=out:st={max(0.0, length - c['fade_out']):.3f}:d={c['fade_out']}")
        post.append(f"trim=duration={length:.3f}")
        fc.append(f"[vf{ci}]{','.join(post)}[vr{ci}]")
        fc.append(f"[os_{i}_{k}]atrim=start={c['in']}:end={c['out']},asetpts=PTS-STARTPTS,volume={c.get('volume', 1.0)}[ao{ci}]")
        fc.append(f"[ds_{i}_{k}]atrim=start={c['in']}:end={c['out']},asetpts=PTS-STARTPTS[ad{ci}]")
        af = [_atempo(sp)]
        if c.get("fade_in"):
            af.append(f"afade=t=in:st=0:d={c['fade_in']}")
        if c.get("fade_out"):
            af.append(f"afade=t=out:st={max(0.0, length - c['fade_out']):.3f}:d={c['fade_out']}")
        af += ["apad", f"atrim=duration={length:.3f}"]
        fc.append(f"[ao{ci}][ad{ci}]amix=inputs=2:normalize=0:duration=first,{','.join(af)}[ar{ci}]")
        concat_in.append(f"[vr{ci}][ar{ci}]")
    fc.append(f"{''.join(concat_in)}concat=n={len(clips)}:v=1:a=1[vc][ac]")

    # --- Pistas en tiempo de salida ---
    out_mix = ["[ac]"]
    k = 0
    for t in tl["tracks"]:
        if t["muted"]:
            continue
        for c in t["clips"]:
            args += ["-i", str(asset_paths[c["asset_id"]])]
            j = n_inputs
            n_inputs += 1
            delay = int(round(c["start"] * 1000))
            fc.append(f"[{j}:a]{AF},atrim=start={c['in']}:end={c['out']},asetpts=PTS-STARTPTS,"
                      f"volume={c['volume'] * t['volume']},adelay={delay}|{delay}[t{k}]")
            out_mix.append(f"[t{k}]")
            k += 1
    if len(out_mix) > 1:
        fc.append(f"{''.join(out_mix)}amix=inputs={len(out_mix)}:normalize=0:duration=first[am]")
    else:
        fc.append("[ac]anull[am]")
    fc.append("[am]alimiter=limit=0.95[aout0]")

    # --- Capas visuales: subtítulos, gráficos, créditos y marca de agua (siempre la última) ---
    total = timeline.output_duration(tl)
    vchain: list[str] = []
    subs = timeline.output_subtitles(tl) if tl["subtitles"]["enabled"] else []
    if subs:
        ass = subtitles.write_ass(subs, workdir / "subs.ass", w, h, tl["subtitles"]["style"])
        vchain.append(f"ass={watermark.filter_escape(str(ass))}")
    for gi, g in enumerate(tl.get("graphics", [])):
        tf = workdir / f"gfx_{gi}.txt"
        tf.write_text(g["text"], encoding="utf-8")
        vchain.append(
            "drawtext=" + ":".join([
                watermark.font_option(), f"textfile={watermark.filter_escape(str(tf))}", "expansion=none",
                f"fontsize=(h*{g['size']})", f"fontcolor={g['color']}", "borderw=2", "bordercolor=black",
                f"x=(w*{g['x']}-text_w/2)", f"y=(h*{g['y']}-text_h/2)",
                f"enable='between(t\\,{g['start']}\\,{g['end']})'",
            ]))
    if credits and tl.get("credits", {}).get("enabled", True):
        secs = min(total, float(tl.get("credits", {}).get("seconds", 4)))
        tf = workdir / "credits.txt"
        tf.write_text("Créditos:\n" + "\n".join(credits), encoding="utf-8")
        vchain.append("drawtext=" + ":".join([
            watermark.font_option(), f"textfile={watermark.filter_escape(str(tf))}", "expansion=none",
            "fontsize=(min(w\\,h)*0.026)", "fontcolor=white", "box=1", "boxcolor=black@0.6", "boxborderw=10",
            "line_spacing=6", "x=(w-text_w)/2", "y=(h*0.06)",
            f"enable='gte(t\\,{max(0.0, total - secs):.3f})'"]))
    vchain += watermark.drawtext_filters(wm_cfg, workdir)
    vchain.append("format=yuv420p")
    fc.append(f"[vc]{','.join(vchain)}[vout0]")

    if opts.start is not None and opts.end is not None:
        s, e = max(0.0, opts.start), min(total, opts.end)
        if e - s < 0.2:
            raise AppError("El fragmento de vista previa es demasiado corto.", code="vista_previa")
        fc.append(f"[vout0]trim=start={s}:end={e},setpts=PTS-STARTPTS[vout]")
        fc.append(f"[aout0]atrim=start={s}:end={e},asetpts=PTS-STARTPTS[aout]")
        total = e - s
    else:
        fc.append("[vout0]null[vout]")
        fc.append("[aout0]anull[aout]")

    script = workdir / "filtergraph.txt"
    script.write_text(";\n".join(fc), encoding="utf-8")
    codec = CODECS[opts.codec]
    crf = opts.crf if opts.crf is not None else (20 if opts.codec == "h264" else 24)
    args += ["-filter_complex_script", str(script), "-map", "[vout]", "-map", "[aout]",
             "-c:v", codec, "-preset", settings.render_preset if opts.resolution != "360p" else "veryfast",
             "-crf", str(crf), "-pix_fmt", "yuv420p"]
    if opts.codec == "h265":
        args += ["-tag:v", "hvc1"]
    args += ["-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", "-t", f"{total:.3f}"]
    for key, value in (metadata or {}).items():
        args += ["-metadata", f"{key}={value}"]
    args.append(str(output))
    return args, total


def render(*, sources: dict[str, Path], tl: dict[str, Any], aspect: str, opts: RenderOptions,
           asset_paths: dict[str, Path], wm_cfg: dict[str, Any], workdir: Path, output: Path,
           metadata: dict[str, str] | None = None, credits: list[str] | None = None,
           on_progress: Callable[[float], None] | None = None,
           should_cancel: Callable[[], bool] | None = None) -> ffmpeg.MediaInfo:
    args, total = build_command(sources=sources, tl=tl, aspect=aspect, opts=opts, asset_paths=asset_paths,
                                wm_cfg=wm_cfg, workdir=workdir, output=output, metadata=metadata, credits=credits)
    ffmpeg.run_with_progress(args, total, on_progress, should_cancel)
    return ffmpeg.probe(output)


def burn_watermark(src: Path, dst: Path, wm_cfg: dict[str, Any], workdir: Path, *,
                   max_height: int = 480, start: float | None = None,
                   duration: float | None = None, should_cancel=None, on_progress=None) -> None:
    """Versión de revisión (baja resolución) de un vídeo intermedio, con marca de agua."""
    vf = [f"scale=-2:'min({max_height},ih)'", "scale=trunc(iw/2)*2:trunc(ih/2)*2"]
    vf += watermark.drawtext_filters(wm_cfg, workdir, prefix="rv")
    vf.append("format=yuv420p")
    args = [settings.ffmpeg, "-y"]
    if start is not None:
        args += ["-ss", f"{start:.3f}"]
    args += ["-i", str(src)]
    if duration is not None:
        args += ["-t", f"{duration:.3f}"]
    args += ["-vf", ",".join(vf), "-c:v", "libx264", "-preset", "veryfast", "-crf", "26",
             "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(dst)]
    total = duration if duration is not None else ffmpeg.probe(src).duration
    ffmpeg.run_with_progress(args, total, on_progress, should_cancel)
