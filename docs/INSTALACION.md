# Instalación, ejecución y mantenimiento

## Requisitos

| Componente | Versión | Notas |
|---|---|---|
| Python | 3.11 – 3.13 | Probado con 3.13 |
| FFmpeg / FFprobe | ≥ 5 con `libx264`, `libass`, `libfreetype` | `libx265` opcional (H.265) |
| OpenCV | **4.x** (`opencv-python-headless<5`) | OpenCV 5 eliminó los clasificadores Haar |

> La versión 0.2 cambia el esquema de la base de datos (varias fuentes por proyecto). Las bases de datos de la 0.1 no se migran: haga copia y elimine `DATA_DIR`.
| Fuente TTF | DejaVu Sans Bold u otra (`FONT_PATH`) | Para la marca de agua |
| GPU NVIDIA | Opcional | Face swap y modelos de lipsync locales |

## Instalación local (desarrollo)

```bash
git clone <repositorio> && cd AEJANDROTOMEMELILLA
python3 -m venv .venv && source .venv/bin/activate
pip install -r backend/requirements-dev.txt
cp .env.example .env            # edite: APP_ENV=development, DATA_DIR=./data, etc.
cd backend
uvicorn app.main:app --reload --port 8000
```

Abra `http://localhost:8000`. En el iPhone, use la IP local del ordenador o, mejor, un túnel HTTPS
(Safari exige HTTPS para el micrófono y para instalar la PWA: *Compartir → Añadir a pantalla de inicio*).

Por defecto (`INLINE_WORKER=true`) la API procesa los trabajos en un hilo propio. Para separarlos:
`INLINE_WORKER=false` y, en otra terminal, `python -m app.worker`.

## Activar los módulos de IA

La aplicación arranca sin IA de pago ni modelos pesados: importación, análisis facial, voces eSpeak,
editor, vista previa y exportación con marca de agua funcionan. Cada módulo se activa en `.env`:

1. **Detección facial mejorada (YuNet, MIT)**: descargue `face_detection_yunet_2023mar.onnx` de OpenCV Zoo → `YUNET_MODEL_PATH`.
2. **Sustitución facial (InsightFace)**: `pip install insightface onnxruntime` (o `onnxruntime-gpu`); coloque `buffalo_l` en `INSIGHTFACE_MODEL_ROOT/models/` y `inswapper_128.onnx` en `INSWAPPER_MODEL_PATH`; tras comprobar la licencia, `FACESWAP_PROVIDER=insightface` e `INSIGHTFACE_LICENSE_ACK=true`.
3. **Sincronización labial**:
   - Remota: `LIPSYNC_PROVIDER=sync_so`, `SYNC_API_KEY=…` y `PUBLIC_BASE_URL` con HTTPS público.
   - Local: instale el modelo (Wav2Lip, LatentSync…) y configure `LIPSYNC_PROVIDER=command` + `LIPSYNC_COMMAND` (ver ejemplos en `.env.example`).
4. **Voces**: `apt install espeak-ng`; Piper (`pip install piper-tts` + voces en `PIPER_VOICES_DIR`); ElevenLabs (`ELEVENLABS_API_KEY`, `ELEVENLABS_VOICE_IDS`).
5. **Subtítulos automáticos**: `pip install faster-whisper`, `ASR_PROVIDER=faster_whisper`.
6. **Fuentes de Internet**: Wikimedia Commons, Internet Archive y enlaces directos funcionan sin claves. Pexels (`PEXELS_API_KEY`), Vimeo (`VIMEO_ACCESS_TOKEN`) y la búsqueda de YouTube (`YOUTUBE_API_KEY`) necesitan claves. Ver [CONECTORES.md](CONECTORES.md).
7. **C2PA**: instale `c2patool` (releases de `contentauth/c2pa-rs`) y, en producción, `C2PA_SIGN_CERT`/`C2PA_PRIVATE_KEY`.

El estado real de cada módulo se ve en **Ajustes → Módulos del servidor**.

## Pruebas automatizadas

```bash
cd backend && python -m pytest -q        # 91 pruebas: unidades, cola, extractor (SSRF, conectores) y flujo completo con FFmpeg real
node --test ../frontend/tests.mjs        # lógica del editor y carga de todos los módulos
```

Las pruebas de extremo a extremo usan FFmpeg real. Los modelos que requieren GPU, pesos con licencia o
claves de pago se sustituyen por **dobles de prueba explícitos** (documentados en `tests/test_flow.py`):
el proveedor de lipsync `command` real ejecuta una orden FFmpeg en lugar de un modelo, y la síntesis usa un
binario falso de eSpeak que recorre la misma ruta de código.

## Mantenimiento

- **Limpieza automática** (cada hora, en el trabajador): temporales > `TEMP_RETENTION_HOURS`, proyectos inactivos > `RETENTION_DAYS`, sesiones caducadas, enlaces de consentimiento caducados, fragmentos enviados a proveedores (6 h).
- **Copias de seguridad**: `sqlite3 data/app.db ".backup copia.db"` + el directorio `data/storage`. Las copias contienen datos personales: cífrelas y aplíqueles la misma política de conservación.
- **Actualizar**: `git pull`, `pip install -r backend/requirements.txt`, reiniciar API y trabajador. El esquema se crea con `CREATE TABLE IF NOT EXISTS`; los cambios incompatibles futuros incluirán migración.
- **Trabajos atascados**: se reencolan solos si no envían latido en `JOB_STALE_SECONDS`. Desde la interfaz se puede cancelar y reintentar.
- **Registros**: salida estándar (`journalctl`/`docker logs`). Los mensajes de error se sanean (sin rutas ni correos).
- **Auditoría**: tabla `audit` (declaraciones, consentimientos, exportaciones con su SHA‑256). Consérvela según su política legal.
