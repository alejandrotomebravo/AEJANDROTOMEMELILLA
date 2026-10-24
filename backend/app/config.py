"""Configuración de la aplicación.

Todos los valores se leen de variables de entorno (o de un archivo ``.env`` en el
directorio de trabajo). Ningún secreto tiene valor por defecto utilizable en
producción. Ver ``.env.example`` en la raíz del repositorio.
"""
from __future__ import annotations

import os
import secrets
import shutil
from dataclasses import dataclass, field
from pathlib import Path


def _load_dotenv(path: Path) -> None:
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def _bool(name: str, default: bool) -> bool:
    v = os.environ.get(name)
    if v is None or v == "":
        return default
    return v.strip().lower() in {"1", "true", "yes", "si", "sí", "on"}


def _int(name: str, default: int) -> int:
    v = os.environ.get(name)
    return int(v) if v not in (None, "") else default


def _float(name: str, default: float) -> float:
    v = os.environ.get(name)
    return float(v) if v not in (None, "") else default


def _str(name: str, default: str = "") -> str:
    return os.environ.get(name, default) or default


@dataclass
class Settings:
    env: str = "development"
    data_dir: Path = Path("data")
    database_path: Path = Path("data/app.db")
    secret_key: str = ""
    public_base_url: str = ""
    cors_origins: list[str] = field(default_factory=list)

    # Límites
    max_upload_mb: int = 1024
    max_video_seconds: int = 600
    max_dialogue_seconds: int = 120
    allow_4k: bool = False
    allow_minors: bool = False
    session_days: int = 14

    # Conservación
    retention_days: int = 30
    temp_retention_hours: int = 24
    consent_link_days: int = 7

    # Cola de trabajos
    inline_worker: bool = True
    worker_concurrency: int = 1
    job_max_attempts: int = 3
    job_stale_seconds: int = 600
    chunk_seconds: float = 10.0

    # Detección facial
    face_detector: str = "auto"  # auto | yunet | haar
    yunet_model_path: str = ""



    # Síntesis de voz
    espeak_binary: str = ""
    piper_command: str = ""
    piper_voices_dir: str = ""

    # Reconocimiento de voz
    asr_provider: str = "none"  # none | faster_whisper
    whisper_model: str = "small"
    whisper_device: str = "cpu"

    # Procedencia
    c2pa_tool: str = ""
    c2pa_sign_cert: str = ""
    c2pa_private_key: str = ""
    c2pa_alg: str = "es256"

    # Render
    font_path: str = ""
    render_preset: str = "medium"
    render_seconds_per_second: dict[str, float] = field(
        default_factory=lambda: {"360p": 0.3, "720p": 0.8, "1080p": 1.6, "2160p": 6.0}
    )

    ffmpeg: str = "ffmpeg"
    ffprobe: str = "ffprobe"
    # Aislamiento de procesos multimedia (límites por subproceso)
    media_cpu_seconds: int = 3600
    media_max_file_mb: int = 8192

    # Generación (v0.3)
    max_scenes: int = 12


    @property
    def storage_dir(self) -> Path:
        return self.data_dir / "storage"

    @property
    def temp_dir(self) -> Path:
        return self.data_dir / "tmp"

    def ensure_dirs(self) -> None:
        for d in (self.data_dir, self.storage_dir, self.temp_dir):
            d.mkdir(parents=True, exist_ok=True)


_FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
    "/Library/Fonts/Arial Bold.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
]


def load_settings() -> Settings:
    _load_dotenv(Path(os.environ.get("ENV_FILE", ".env")))
    data_dir = Path(_str("DATA_DIR", "data")).resolve()
    env = _str("APP_ENV", "development")
    secret = _str("SECRET_KEY")
    if not secret:
        if env == "production":
            raise RuntimeError("SECRET_KEY es obligatorio en producción")
        secret = secrets.token_hex(32)
    font = _str("FONT_PATH")
    if not font:
        font = next((f for f in _FONT_CANDIDATES if Path(f).is_file()), "")
    s = Settings(
        env=env,
        data_dir=data_dir,
        database_path=Path(_str("DATABASE_PATH", str(data_dir / "app.db"))),
        secret_key=secret,
        public_base_url=_str("PUBLIC_BASE_URL").rstrip("/"),
        cors_origins=[o.strip() for o in _str("CORS_ORIGINS").split(",") if o.strip()],
        max_upload_mb=_int("MAX_UPLOAD_MB", 1024),
        max_video_seconds=_int("MAX_VIDEO_SECONDS", 600),
        max_dialogue_seconds=_int("MAX_DIALOGUE_SECONDS", 120),
        allow_4k=_bool("ALLOW_4K", False),
        allow_minors=_bool("ALLOW_MINORS", False),
        session_days=_int("SESSION_DAYS", 14),
        retention_days=_int("RETENTION_DAYS", 30),
        temp_retention_hours=_int("TEMP_RETENTION_HOURS", 24),
        consent_link_days=_int("CONSENT_LINK_DAYS", 7),
        inline_worker=_bool("INLINE_WORKER", True),
        worker_concurrency=_int("WORKER_CONCURRENCY", 1),
        job_max_attempts=_int("JOB_MAX_ATTEMPTS", 3),
        job_stale_seconds=_int("JOB_STALE_SECONDS", 600),
        chunk_seconds=_float("CHUNK_SECONDS", 10.0),
        face_detector=_str("FACE_DETECTOR", "auto"),
        yunet_model_path=_str("YUNET_MODEL_PATH"),
        espeak_binary=_str("ESPEAK_BINARY") or (shutil.which("espeak-ng") or shutil.which("espeak") or ""),
        piper_command=_str("PIPER_COMMAND") or (shutil.which("piper") or ""),
        piper_voices_dir=_str("PIPER_VOICES_DIR"),
        asr_provider=_str("ASR_PROVIDER", "none"),
        whisper_model=_str("WHISPER_MODEL", "small"),
        whisper_device=_str("WHISPER_DEVICE", "cpu"),
        c2pa_tool=_str("C2PA_TOOL") or (shutil.which("c2patool") or ""),
        c2pa_sign_cert=_str("C2PA_SIGN_CERT"),
        c2pa_private_key=_str("C2PA_PRIVATE_KEY"),
        c2pa_alg=_str("C2PA_ALG", "es256"),
        font_path=font,
        render_preset=_str("RENDER_PRESET", "medium"),
        ffmpeg=_str("FFMPEG_BINARY", "ffmpeg"),
        ffprobe=_str("FFPROBE_BINARY", "ffprobe"),
        media_cpu_seconds=_int("MEDIA_CPU_SECONDS", 3600),
        media_max_file_mb=_int("MEDIA_MAX_FILE_MB", 8192),
        max_scenes=_int("MAX_SCENES", 12),
    )
    return s


settings = load_settings()


def reload_settings() -> Settings:
    """Recarga la configuración (útil en pruebas)."""
    global settings
    new = load_settings()
    settings.__dict__.update(new.__dict__)
    return settings
