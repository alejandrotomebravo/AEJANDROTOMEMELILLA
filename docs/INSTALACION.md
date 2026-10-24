# Instalación, ejecución y mantenimiento

## Requisitos

| Componente | Versión | Notas |
|---|---|---|
| Python | 3.11 – 3.13 | Probado con 3.13 |
| FFmpeg / FFprobe | ≥ 5 con `libx264`, `libass`, `libfreetype` | `libx265` opcional (H.265) |
| OpenCV | **4.x** (`opencv-python-headless<5`) | OpenCV 5 eliminó los clasificadores Haar |

| Fuente TTF | DejaVu Sans Bold u otra (`FONT_PATH`) | Para la marca de agua |
| Claves de API | Opcionales | `ANTHROPIC_API_KEY` (guion), `FAL_KEY` (imágenes y vídeo) |

> La versión 0.3 (generación de vídeo) cambia el esquema de la base de datos (v3). Las bases de datos anteriores no se migran: haga copia y use un `DATA_DIR` nuevo.

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

## Activar la generación con IA

Sin claves, la aplicación funciona en **modo local gratuito** (guion básico, filtros, animática) para probar todo el
flujo. Para generar con IA, en `.env`:

1. **Guion con Claude**: `ANTHROPIC_API_KEY=…` (consola de Anthropic). Opcional: `ANTHROPIC_MODEL`.
2. **Imágenes y vídeo con fal.ai**: `FAL_KEY=…` (panel de fal.ai). Modelos y precios configurables (`FAL_*`).
   Revise y ajuste `FAL_IMAGE_COST` y `FAL_VIDEO_COST_PER_SECOND` a la tarifa vigente: son los importes que la app
   muestra antes de cada generación.
3. **Voces**: `apt install espeak-ng`; Piper (`pip install piper-tts` + voces en `PIPER_VOICES_DIR`); ElevenLabs
   (`ELEVENLABS_API_KEY`, `ELEVENLABS_VOICE_IDS`).
4. **Detección facial mejorada para validar fotos (YuNet, MIT)**: `YUNET_MODEL_PATH`.
5. **Subtítulos automáticos**: `pip install faster-whisper`, `ASR_PROVIDER=faster_whisper`.
6. **C2PA**: `c2patool` y, en producción, `C2PA_SIGN_CERT`/`C2PA_PRIVATE_KEY`.

Con `*_PROVIDER=auto` (por defecto) se usa el servicio si su clave existe. El estado real se ve en
**Ajustes → Módulos del servidor**.

## Pruebas automatizadas

```bash
cd backend && python -m pytest -q        # 89 pruebas: unidades, cola, red segura, generador y flujo completo con FFmpeg real
node --test ../frontend/tests.mjs        # lógica del editor y carga de todos los módulos
```

Las pruebas no llaman a servicios de pago: Claude y fal.ai se sustituyen por **dobles de prueba explícitos**
(`tests/test_gen.py`) que comprueban la forma exacta de las peticiones, la cola de fal.ai, la reanudación sin
volver a pagar, los rechazos y los filtros. El flujo de extremo a extremo (`tests/test_flow.py`) usa los
proveedores locales reales y un binario falso de eSpeak que recorre la misma ruta de síntesis.

## Mantenimiento

- **Limpieza automática** (cada hora, en el trabajador): temporales > `TEMP_RETENTION_HOURS`, proyectos inactivos > `RETENTION_DAYS`, sesiones caducadas, enlaces de consentimiento caducados, archivos temporales de los trabajos.
- **Copias de seguridad**: `sqlite3 data/app.db ".backup copia.db"` + el directorio `data/storage`. Las copias contienen datos personales: cífrelas y aplíqueles la misma política de conservación.
- **Actualizar**: `git pull`, `pip install -r backend/requirements.txt`, reiniciar API y trabajador. El esquema se crea con `CREATE TABLE IF NOT EXISTS`; los cambios incompatibles futuros incluirán migración.
- **Trabajos atascados**: se reencolan solos si no envían latido en `JOB_STALE_SECONDS`. Desde la interfaz se puede cancelar y reintentar.
- **Registros**: salida estándar (`journalctl`/`docker logs`). Los mensajes de error se sanean (sin rutas ni correos).
- **Auditoría**: tabla `audit` (declaraciones, consentimientos, exportaciones con su SHA‑256). Consérvela según su política legal.
