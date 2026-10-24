"""Envoltorio de FFmpeg/FFprobe.

Todas las operaciones se ejecutan como subprocesos con listas de argumentos
(sin shell) para evitar inyecciones a través de nombres de archivo.
"""
from __future__ import annotations

import json
import math
import re
import subprocess
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Callable

from ..config import settings
from ..errors import AppError, JobCancelled


class FFmpegError(AppError):
    code = "ffmpeg"
    status = 500


class UnsupportedMedia(AppError):
    code = "formato_no_admitido"
    status = 415


# Contenedores aceptados para archivos de usuarios o de Internet. Se excluyen
# formatos que pueden hacer que FFmpeg lea otros archivos o URLs (listas HLS,
# «concat», secuencias de imágenes, etc.).
VIDEO_FORMATS = {"mov", "mp4", "m4a", "3gp", "3g2", "mj2", "matroska", "webm", "avi", "ogg", "mpegts", "mpeg", "flv"}
AUDIO_FORMATS = VIDEO_FORMATS | {"wav", "mp3", "aac", "flac", "caf", "aiff", "w64"}
UNTRUSTED_INPUT = ["-protocol_whitelist", "file"]


def _limits() -> None:  # pragma: no cover - se ejecuta en el proceso hijo
    """Límites de recursos del subproceso (aislamiento básico frente a archivos malformados)."""
    try:
        import resource
        cpu = settings.media_cpu_seconds
        resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu + 30))
        fsize = settings.media_max_file_mb * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_FSIZE, (fsize, fsize))
    except Exception:
        pass


@dataclass
class MediaInfo:
    duration: float
    width: int | None
    height: int | None
    fps: float | None
    vcodec: str | None
    acodec: str | None
    has_audio: bool
    has_video: bool
    rotation: int
    sample_rate: int | None
    format_name: str

    @property
    def display_size(self) -> tuple[int | None, int | None]:
        if self.rotation in (90, 270, -90) and self.width and self.height:
            return self.height, self.width
        return self.width, self.height


def run(args: list[str], *, timeout: int | None = None) -> subprocess.CompletedProcess:
    proc = subprocess.run(args, capture_output=True, text=True, timeout=timeout, preexec_fn=_limits)
    if proc.returncode != 0:
        tail = "\n".join(proc.stderr.strip().splitlines()[-8:])
        raise FFmpegError(f"Fallo en el procesamiento multimedia: {tail}")
    return proc


def _fps(rate: str | None) -> float | None:
    if not rate or rate in ("0/0",):
        return None
    if "/" in rate:
        n, d = rate.split("/")
        return float(n) / float(d) if float(d) else None
    return float(rate)


def probe(path: str | Path, untrusted: str | None = None) -> MediaInfo:
    """Analiza un archivo. ``untrusted`` = "video" | "audio" aplica lista blanca de contenedores."""
    extra = ["-protocol_whitelist", "file"] if untrusted else []
    try:
        proc = run([settings.ffprobe, "-v", "error", *extra, "-print_format", "json", "-show_format",
                    "-show_streams", str(path)], timeout=60)
    except FFmpegError:
        if untrusted:
            raise UnsupportedMedia("El archivo no es un vídeo o audio válido o está dañado.")
        raise
    data = json.loads(proc.stdout or "{}")
    streams = data.get("streams", [])
    v = next((s for s in streams if s.get("codec_type") == "video"
              and not s.get("disposition", {}).get("attached_pic")), None)
    a = next((s for s in streams if s.get("codec_type") == "audio"), None)
    fmt = data.get("format", {})
    if untrusted:
        names = set((fmt.get("format_name") or "").split(","))
        allowed = VIDEO_FORMATS if untrusted == "video" else AUDIO_FORMATS
        if not names & allowed:
            raise UnsupportedMedia(f"Formato de archivo no admitido ({fmt.get('format_name')}).")
    try:
        duration = float(fmt.get("duration") or (v or a or {}).get("duration") or 0)
    except ValueError:
        duration = 0.0
    rotation = 0
    if v:
        tags = v.get("tags", {})
        if "rotate" in tags:
            rotation = int(tags["rotate"])
        for sd in v.get("side_data_list", []) or []:
            if "rotation" in sd:
                rotation = int(sd["rotation"])
    return MediaInfo(
        duration=duration,
        width=int(v["width"]) if v else None,
        height=int(v["height"]) if v else None,
        fps=_fps(v.get("avg_frame_rate") or v.get("r_frame_rate")) if v else None,
        vcodec=v.get("codec_name") if v else None,
        acodec=a.get("codec_name") if a else None,
        has_audio=a is not None,
        has_video=v is not None,
        rotation=abs(rotation) % 360,
        sample_rate=int(a["sample_rate"]) if a and a.get("sample_rate") else None,
        format_name=fmt.get("format_name", ""),
    )


@lru_cache(maxsize=1)
def encoders() -> set[str]:
    proc = subprocess.run([settings.ffmpeg, "-hide_banner", "-encoders"], capture_output=True, text=True)
    found = set()
    for line in proc.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2 and len(parts[0]) == 6:
            found.add(parts[1])
    return found


def has_encoder(name: str) -> bool:
    return name in encoders()


def run_with_progress(args: list[str], total_seconds: float,
                      on_progress: Callable[[float], None] | None = None,
                      should_cancel: Callable[[], bool] | None = None) -> None:
    """Ejecuta ffmpeg leyendo ``-progress`` para informar del avance (0..1)."""
    full = [args[0], "-hide_banner", "-nostats", "-progress", "pipe:1", *args[1:]]
    proc = subprocess.Popen(full, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, preexec_fn=_limits)
    stderr_tail: list[str] = []
    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            if should_cancel and should_cancel():
                proc.kill()
                raise JobCancelled()
            if line.startswith("out_time_us=") or line.startswith("out_time_ms="):
                try:
                    us = int(line.split("=", 1)[1].strip())
                except ValueError:
                    continue
                if on_progress and total_seconds > 0:
                    on_progress(min(1.0, us / 1_000_000 / total_seconds))
        proc.wait()
        assert proc.stderr is not None
        stderr_tail = proc.stderr.read().strip().splitlines()[-8:]
    finally:
        if proc.poll() is None:
            proc.kill()
    if proc.returncode != 0:
        raise FFmpegError("Fallo en el procesamiento multimedia: " + "\n".join(stderr_tail))


def normalize_video(src: Path, dst: Path, *, max_height: int | None = None, fps: float | None = None,
                    start: float | None = None, end: float | None = None,
                    on_progress=None, should_cancel=None) -> MediaInfo:
    """Convierte la entrada a MP4 H.264/AAC con fotogramas constantes.

    Aplica la rotación de los metadatos (habitual en vídeos de iPhone) y añade
    una pista de audio silenciosa si el original no tiene audio, para que el
    resto de la cadena pueda asumir que siempre existe.
    """
    info = probe(src, untrusted="video")
    if not info.has_video:
        raise AppError("El archivo no contiene una pista de vídeo.", code="sin_video")
    if not info.duration or info.duration <= 0:
        raise UnsupportedMedia("No se puede determinar la duración del vídeo (archivo dañado o incompleto).")
    start = max(0.0, start or 0.0)
    end = min(info.duration, end) if end else info.duration
    if end - start < 0.5:
        raise AppError("El fragmento seleccionado es demasiado corto.", code="fragmento")
    span = end - start
    target_fps = fps or (info.fps if info.fps and 10 <= info.fps <= 60 else 30)
    target_fps = round(target_fps, 3)
    vf = [f"fps={target_fps}"]
    if max_height:
        vf.append(f"scale=-2:'min({max_height},ih)'")
    vf.append("scale=trunc(iw/2)*2:trunc(ih/2)*2")
    vf.append("format=yuv420p")
    args = [settings.ffmpeg, "-y", *UNTRUSTED_INPUT, "-ss", f"{start:.3f}", "-i", str(src)]
    if not info.has_audio:
        args += ["-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000"]
    args += ["-map", "0:v:0", "-map", "0:a:0" if info.has_audio else "1:a:0",
             "-vf", ",".join(vf), "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
             "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
             "-movflags", "+faststart", "-t", f"{span:.3f}", "-map_metadata", "-1", str(dst)]
    run_with_progress(args, span, on_progress, should_cancel)
    return probe(dst)


def extract_audio_wav(src: Path, dst: Path, *, start: float | None = None,
                      duration: float | None = None, sample_rate: int = 48000, mono: bool = False) -> None:
    args = [settings.ffmpeg, "-y"]
    if start is not None:
        args += ["-ss", f"{start:.3f}"]
    args += ["-i", str(src)]
    if duration is not None:
        args += ["-t", f"{duration:.3f}"]
    args += ["-vn", "-ac", "1" if mono else "2", "-ar", str(sample_rate), "-c:a", "pcm_s16le", str(dst)]
    run(args)


def to_wav(src: Path, dst: Path, sample_rate: int = 48000) -> MediaInfo:
    """Convierte audio de origen no confiable a WAV PCM."""
    info = probe(src, untrusted="audio")
    if not info.has_audio:
        raise UnsupportedMedia("El archivo no contiene audio.")
    run([settings.ffmpeg, "-y", *UNTRUSTED_INPUT, "-i", str(src), "-vn", "-ac", "2", "-ar", str(sample_rate),
         "-c:a", "pcm_s16le", "-map_metadata", "-1", str(dst)])
    return probe(dst)


def sprite(src: Path, dst: Path, duration: float, max_frames: int = 60, width: int = 160) -> dict:
    """Tira de fotogramas de previsualización en una sola imagen (mosaico)."""
    interval = max(1.0, duration / max_frames)
    n = max(1, int(math.ceil(duration / interval)))
    cols = min(10, n)
    rows = int(math.ceil(n / cols))
    run([settings.ffmpeg, "-y", "-i", str(src), "-vf",
         f"fps=1/{interval:.4f},scale={width}:-2,tile={cols}x{rows}", "-frames:v", "1", "-q:v", "5", str(dst)])
    return {"interval": round(interval, 4), "count": n, "cols": cols, "rows": rows, "tile_width": width}


def cut_segment(src: Path, dst: Path, start: float, duration: float, *, with_audio: bool = True) -> None:
    """Corta con precisión de fotograma (recodifica)."""
    args = [settings.ffmpeg, "-y", "-ss", f"{start:.3f}", "-i", str(src), "-t", f"{duration:.3f}",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "16", "-pix_fmt", "yuv420p"]
    args += ["-c:a", "aac", "-b:a", "192k"] if with_audio else ["-an"]
    args.append(str(dst))
    run(args)


def thumbnail(src: Path, dst: Path, t: float, width: int = 320) -> None:
    run([settings.ffmpeg, "-y", "-ss", f"{max(0.0, t):.3f}", "-i", str(src), "-frames:v", "1",
         "-vf", f"scale={width}:-2", "-q:v", "4", str(dst)])


_SIL_START = re.compile(r"silence_start: (-?[\d.]+)")
_SIL_END = re.compile(r"silence_end: (-?[\d.]+)")


def detect_speech_segments(src: Path, duration: float, noise_db: float = -35,
                           min_silence: float = 0.4) -> list[dict]:
    """Segmentos de audio con actividad (complemento de los silencios detectados).

    No distingue voz de música: es una detección por energía, se etiqueta como
    «actividad de audio» en la interfaz.
    """
    proc = subprocess.run(
        [settings.ffmpeg, "-hide_banner", "-nostats", "-i", str(src), "-vn",
         "-af", f"silencedetect=noise={noise_db}dB:d={min_silence}", "-f", "null", "-"],
        capture_output=True, text=True,
    )
    silences: list[tuple[float, float]] = []
    start = None
    for line in proc.stderr.splitlines():
        m = _SIL_START.search(line)
        if m:
            start = max(0.0, float(m.group(1)))
            continue
        m = _SIL_END.search(line)
        if m and start is not None:
            silences.append((start, float(m.group(1))))
            start = None
    if start is not None:
        silences.append((start, duration))
    segments = []
    cursor = 0.0
    for s, e in silences:
        if s - cursor > 0.15:
            segments.append({"start": round(cursor, 3), "end": round(s, 3)})
        cursor = e
    if duration - cursor > 0.15:
        segments.append({"start": round(cursor, 3), "end": round(duration, 3)})
    return segments


_PTS = re.compile(r"pts_time:([\d.]+)")


def detect_scenes(src: Path, threshold: float = 0.3) -> list[float]:
    proc = subprocess.run(
        [settings.ffmpeg, "-hide_banner", "-nostats", "-i", str(src), "-an",
         "-vf", f"select='gt(scene,{threshold})',showinfo", "-f", "null", "-"],
        capture_output=True, text=True,
    )
    cuts = []
    for line in proc.stderr.splitlines():
        if "showinfo" in line and "pts_time" in line:
            m = _PTS.search(line)
            if m:
                cuts.append(round(float(m.group(1)), 3))
    return cuts


def atempo_chain(factor: float) -> list[str]:
    """atempo admite 0.5..2.0 por etapa: se encadena para otros valores."""
    parts = []
    f = factor
    while f > 2.0:
        parts.append("atempo=2.0")
        f /= 2.0
    while f < 0.5:
        parts.append("atempo=0.5")
        f /= 0.5
    parts.append(f"atempo={f:.5f}")
    return parts


def process_audio(src: Path, dst: Path, *, tempo: float = 1.0, semitones: float = 0.0,
                  gain_db: float = 0.0, sample_rate: int = 48000) -> MediaInfo:
    filters: list[str] = []
    if abs(semitones) > 0.01:
        ratio = 2 ** (semitones / 12)
        filters.append(f"asetrate={sample_rate}*{ratio:.6f}")
        filters.append(f"aresample={sample_rate}")
        filters += atempo_chain(1 / ratio)
    if abs(tempo - 1.0) > 0.001:
        filters += atempo_chain(tempo)
    if abs(gain_db) > 0.01:
        filters.append(f"volume={gain_db}dB")
    args = [settings.ffmpeg, "-y", "-i", str(src), "-ar", str(sample_rate), "-ac", "2"]
    if filters:
        args += ["-af", ",".join(filters)]
    args += ["-c:a", "pcm_s16le", str(dst)]
    run(args)
    return probe(dst)


def silence_wav(dst: Path, seconds: float, sample_rate: int = 48000) -> None:
    run([settings.ffmpeg, "-y", "-f", "lavfi", "-i",
         f"anullsrc=channel_layout=stereo:sample_rate={sample_rate}", "-t", f"{seconds:.3f}",
         "-c:a", "pcm_s16le", str(dst)])


def concat_audio(parts: list[Path], dst: Path, sample_rate: int = 48000) -> MediaInfo:
    args = [settings.ffmpeg, "-y"]
    for p in parts:
        args += ["-i", str(p)]
    norm = "".join(
        f"[{i}:a]aresample={sample_rate},aformat=channel_layouts=stereo[a{i}];" for i in range(len(parts)))
    inputs = "".join(f"[a{i}]" for i in range(len(parts)))
    args += ["-filter_complex", f"{norm}{inputs}concat=n={len(parts)}:v=0:a=1[out]",
             "-map", "[out]", "-c:a", "pcm_s16le", str(dst)]
    run(args)
    return probe(dst)
