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

    # Sustitución facial
    faceswap_provider: str = "none"  # none | insightface
    insightface_model_root: str = ""
    inswapper_model_path: str = ""
    insightface_license_ack: bool = False
    faceswap_device: str = "cpu"  # cpu | cuda
    faceswap_seconds_per_second_cpu: float = 30.0
    faceswap_seconds_per_second_gpu: float = 1.5

    # Sincronización labial
    lipsync_provider: str = "none"  # none | sync_so | command
    sync_api_key: str = ""
    sync_api_base: str = "https://api.sync.so/v2"
    sync_model: str = "lipsync-2"
    sync_cost_per_second: float = 0.05
    sync_seconds_per_second: float = 3.0
    sync_poll_seconds: float = 5.0
    sync_timeout_seconds: int = 1800
    lipsync_command: str = ""
    lipsync_command_license: str = ""
    lipsync_command_seconds_per_second: float = 20.0

    # Síntesis de voz
    espeak_binary: str = ""
    piper_command: str = ""
    piper_voices_dir: str = ""
    elevenlabs_api_key: str = ""
    elevenlabs_api_base: str = "https://api.elevenlabs.io/v1"
    elevenlabs_model: str = "eleven_multilingual_v2"
    elevenlabs_voice_ids: str = ""
    elevenlabs_cost_per_1k_chars: float = 0.30

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

    # Importación desde Internet
    http_user_agent: str = "EstudioIA/0.2 (+https://example.invalid/contacto)"
    outbound_proxy: str = ""
    allow_private_networks: bool = False  # SOLO pruebas: permite descargar de IPs privadas
    download_timeout_seconds: int = 60
    max_redirects: int = 5
    youtube_api_key: str = ""
    vimeo_access_token: str = ""
    pexels_api_key: str = ""
    enabled_connectors: str = "wikimedia,internet_archive,pexels,vimeo,youtube,direct_url"

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
        faceswap_provider=_str("FACESWAP_PROVIDER", "none"),
        insightface_model_root=_str("INSIGHTFACE_MODEL_ROOT"),
        inswapper_model_path=_str("INSWAPPER_MODEL_PATH"),
        insightface_license_ack=_bool("INSIGHTFACE_LICENSE_ACK", False),
        faceswap_device=_str("FACESWAP_DEVICE", "cpu"),
        faceswap_seconds_per_second_cpu=_float("FACESWAP_SECONDS_PER_SECOND_CPU", 30.0),
        faceswap_seconds_per_second_gpu=_float("FACESWAP_SECONDS_PER_SECOND_GPU", 1.5),
        lipsync_provider=_str("LIPSYNC_PROVIDER", "none"),
        sync_api_key=_str("SYNC_API_KEY"),
        sync_api_base=_str("SYNC_API_BASE", "https://api.sync.so/v2").rstrip("/"),
        sync_model=_str("SYNC_MODEL", "lipsync-2"),
        sync_cost_per_second=_float("SYNC_COST_PER_SECOND", 0.05),
        sync_seconds_per_second=_float("SYNC_SECONDS_PER_SECOND", 3.0),
        sync_poll_seconds=_float("SYNC_POLL_SECONDS", 5.0),
        sync_timeout_seconds=_int("SYNC_TIMEOUT_SECONDS", 1800),
        lipsync_command=_str("LIPSYNC_COMMAND"),
        lipsync_command_license=_str("LIPSYNC_COMMAND_LICENSE"),
        lipsync_command_seconds_per_second=_float("LIPSYNC_COMMAND_SECONDS_PER_SECOND", 20.0),
        espeak_binary=_str("ESPEAK_BINARY") or (shutil.which("espeak-ng") or shutil.which("espeak") or ""),
        piper_command=_str("PIPER_COMMAND") or (shutil.which("piper") or ""),
        piper_voices_dir=_str("PIPER_VOICES_DIR"),
        elevenlabs_api_key=_str("ELEVENLABS_API_KEY"),
        elevenlabs_api_base=_str("ELEVENLABS_API_BASE", "https://api.elevenlabs.io/v1").rstrip("/"),
        elevenlabs_model=_str("ELEVENLABS_MODEL", "eleven_multilingual_v2"),
        elevenlabs_voice_ids=_str("ELEVENLABS_VOICE_IDS"),
        elevenlabs_cost_per_1k_chars=_float("ELEVENLABS_COST_PER_1K_CHARS", 0.30),
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
        http_user_agent=_str("HTTP_USER_AGENT", "EstudioIA/0.2 (+https://example.invalid/contacto)"),
        outbound_proxy=_str("OUTBOUND_PROXY"),
        allow_private_networks=_bool("ALLOW_PRIVATE_NETWORKS", False),
        download_timeout_seconds=_int("DOWNLOAD_TIMEOUT_SECONDS", 60),
        max_redirects=_int("MAX_REDIRECTS", 5),
        youtube_api_key=_str("YOUTUBE_API_KEY"),
        vimeo_access_token=_str("VIMEO_ACCESS_TOKEN"),
        pexels_api_key=_str("PEXELS_API_KEY"),
        enabled_connectors=_str("ENABLED_CONNECTORS", "wikimedia,internet_archive,pexels,vimeo,youtube,direct_url"),
    )
    if s.env == "production" and s.allow_private_networks:
        raise RuntimeError("ALLOW_PRIVATE_NETWORKS no puede activarse en producción (riesgo de SSRF).")
    return s


settings = load_settings()


def reload_settings() -> Settings:
    """Recarga la configuración (útil en pruebas)."""
    global settings
    new = load_settings()
    settings.__dict__.update(new.__dict__)
    return settings
