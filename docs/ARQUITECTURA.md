# Arquitectura técnica (Fase 1)

> Estado a **24/10/2026**. Las licencias, precios y disponibilidad de servicios de terceros cambian con frecuencia:
> verifíquelos antes de un uso comercial. Donde las fuentes consultadas eran contradictorias se indica expresamente.

## 1. Visión general

```
┌───────────────────────────┐      HTTPS       ┌─────────────────────────────────────────────┐
│ PWA (HTML + JS sin build) │ ───────────────▶ │ API FastAPI (Python 3.11+)                  │
│ iPhone · Android · PC     │ ◀─────────────── │  · auth, proyectos, identidades, consentim. │
│ - asistente de 8 pasos    │   URL firmadas   │  · validación de políticas y marca de agua   │
│ - editor multipista       │   (vídeo/audio)  │  · cola de trabajos persistente (SQLite)     │
└───────────────────────────┘                  └───────────────┬─────────────────────────────┘
                                                               │ misma BD + volumen de datos
                                               ┌───────────────▼─────────────────────────────┐
                                               │ Trabajador(es)  python -m app.worker        │
                                               │  FFmpeg · OpenCV · proveedores de IA        │
                                               │  ┌──────────────┐ ┌──────────────────────┐  │
                                               │  │ Face swap    │ │ Lipsync              │  │
                                               │  │ InsightFace* │ │ sync.so (remoto $)   │  │
                                               │  └──────────────┘ │ comando local (GPU)  │  │
                                               │  ┌──────────────┐ └──────────────────────┘  │
                                               │  │ TTS: eSpeak, │ ┌──────────────────────┐  │
                                               │  │ Piper, 11Labs│ │ ASR: faster-whisper  │  │
                                               │  └──────────────┘ └──────────────────────┘  │
                                               └─────────────────────────────────────────────┘
```

**Principio rector:** cada capacidad de IA está detrás de una interfaz (`FaceSwapProvider`, `LipsyncProvider`,
voces de `tts`, `asr`) y se selecciona por configuración. La interfaz consulta `/api/system/capabilities` y
desactiva lo que no esté disponible: **no se simula ninguna función**.

## 2. Decisiones y justificación

| Decisión | Alternativas evaluadas | Motivo |
|---|---|---|
| **FastAPI + Python** | Node/NestJS, Go | Todo el ecosistema de IA audiovisual (InsightFace, Whisper, OpenCV, modelos de lipsync) es Python: evita una frontera entre lenguajes. |
| **PWA sin paso de compilación** (ES modules) | React/Vite, app nativa | Instalable en iPhone desde Safari, acceso a galería (`<input type=file accept=video/*>`) y micrófono (MediaRecorder). Sin cadena de build que mantener. Una app nativa no aporta nada imprescindible en la Fase 2. |
| **SQLite (WAL) + cola propia persistente** | PostgreSQL + Redis/RQ/Celery | Un único nodo cubre el caso de uso inicial con cero servicios adicionales. La cola soporta reintentos, puntos de control, cancelación y recuperación ante caídas. El acceso está encapsulado en `db.py`/`jobs/queue.py` para migrar a PostgreSQL + Redis al escalar (Fase 4+). |
| **FFmpeg** por subproceso | PyAV, MoviePy | Estándar de facto, H.264/H.265, `drawtext`, `ass`, progreso fiable con `-progress`. Sin `shell=True` (seguridad). |
| **OpenCV** para detección/seguimiento | MediaPipe, InsightFace det | Licencia permisiva; YuNet (MIT) da 5 puntos faciales para estimar el giro; Haar como respaldo sin descargas. **OpenCV 5 eliminó Haar** → se fija `opencv-python-headless<5`. |
| **Marca de agua en el servidor** | Superposición en el cliente | Una marca aplicada en el navegador se elimina trivialmente. Se graba en los píxeles en *todas* las salidas, y los vídeos intermedios sin marca nunca se sirven. |
| **Línea de tiempo con dos bases temporales** | Una sola base | Diálogos y silencios ligados a la imagen (tiempo de origen) siguen a la escena al reordenarla; música y gráficos van en tiempo de salida. |

## 3. Proveedores de IA: licencias, costes y requisitos

### 3.1 Sustitución facial
| Opción | Licencia (verificada) | Hardware | Integración |
|---|---|---|---|
| **InsightFace `inswapper_128`** | Código MIT; **modelos sólo para investigación no comercial**. Desde nov‑2025 el README indica que inswapper requiere licencia aparte de InsightFace para uso comercial. | CPU (≈10‑15 s por s de vídeo) o GPU CUDA (≈1‑2 s/s) | ✅ Implementado (`ai/face_swap.py`). Exige `INSIGHTFACE_LICENSE_ACK=true`; los pesos no se descargan automáticamente. |
| API comercial de face swap | Variable | — | ❌ No implementado: no se ha verificado ninguna API con condiciones adecuadas. La interfaz `FaceSwapProvider` permite añadirla. |

> ⚠ Para un producto **comercial** no existe, a fecha de hoy, un modelo abierto de face swap de calidad con licencia comercial clara. Hay que licenciar InsightFace o contratar un proveedor.

### 3.2 Sincronización labial
| Opción | Licencia / precio | Hardware | Integración |
|---|---|---|---|
| **sync.so** (Sync Labs) API v2 | Comercial. Docs: `lipsync-2` ≈ 0,04‑0,05 USD/s; `lipsync-2-pro` ≈ 0,067‑0,083 USD/s; plan gratuito 3 generaciones/mes de ≤20 s. Precios de terceros (fal.ai) difieren. | Remoto | ✅ Implementado: `POST /v2/generate` con `x-api-key`, URL firmadas temporales, sondeo de estado, `Idempotency-Key` para no cobrar dos veces en reintentos. Requiere `PUBLIC_BASE_URL` accesible desde Internet. |
| **LatentSync** (ByteDance) | **Fuentes contradictorias**: Apache‑2.0 según varias, OpenRAIL++ según otra. Revisar el LICENSE del repositorio. | 1.5: ~8 GB VRAM; 1.6 (512 px): ~18 GB VRAM | ✅ Vía proveedor `command` (plantilla configurable). |
| **Wav2Lip** | Sólo investigación/no comercial (comercial vía Sync Labs). | GPU 4‑6 GB recomendado | ✅ Vía `command`. |
| **MuseTalk** | Revisar licencia de código y pesos. | GPU | ✅ Vía `command`. |

Ejemplo de `LIPSYNC_COMMAND` para LatentSync o Wav2Lip en `.env.example`.

### 3.3 Voz
| Opción | Licencia / precio | Integración |
|---|---|---|
| **eSpeak NG** | GPL‑3.0, gratuito, voz robótica | ✅ |
| **Piper** (`piper1-gpl`) | Motor GPL‑3.0 (antes MIT); **cada voz tiene su propia licencia** (MODEL_CARD). | ✅ Plantilla de orden configurable. |
| **ElevenLabs** | Comercial, por caracteres (`ELEVENLABS_COST_PER_1K_CHARS` configurable) | ✅ `POST /v1/text-to-speech/{voice_id}` con `xi-api-key`. Sólo voces de catálogo autorizadas por el administrador. |
| Clonación de voz | — | ❌ **No implementada deliberadamente** en la Fase 2 por su riesgo de suplantación; requeriría consentimiento de voz verificado (ya modelado en `identities.scopes`). |

### 3.4 Reconocimiento de voz y subtítulos
`faster-whisper` (MIT) opcional. Sin él, los subtítulos se generan del texto del diálogo con tiempos por sílabas y
segmentos reales de la síntesis, o se escriben a mano.

### 3.5 Procedencia
`c2patool` (CAI, MIT/Apache‑2.0) si está instalado: manifiesto `c2pa.actions` con
`digitalSourceType = compositeWithTrainedAlgorithmicMedia`. Sin certificado propio usa un certificado de pruebas.
Siempre se añaden etiquetas MP4 y un JSON de procedencia (no firmado).

## 4. Flujo de datos de un proyecto

1. **Proyecto + declaración de uso** (finalidad + 6 compromisos obligatorios, registrados en `audit`).
2. **Procedencia del vídeo** (propio / licencia / autorizado / CC). Rechaza «descargado de Internet» sin licencia y CC‑ND; avisa de NC/SA.
3. **Importación** → validación con `ffprobe` (no por extensión) → *proxy* H.264 CFR (aplica rotación de iPhone) → **análisis**: rostros + seguimiento, actividad de audio, cortes de escena.
4. **Identidades**: autodeclaración (yo) o **enlace de consentimiento** personal y caducable para terceros (texto versionado con hash, IP/agente seudonimizados, revocable). Menores bloqueados por defecto.
5. **Face swap** por fragmentos de `CHUNK_SECONDS` con puntos de control → nueva versión del vídeo de trabajo (historial reversible) → copia de revisión 480p **con marca**.
6. **Diálogo** A/B/C → audio WAV + segmentos + subtítulos.
7. **Lipsync**: informe de calidad previo (bloqueos/advertencias) → confirmación de coste si es remoto → fragmento → proveedor → comprobación posterior → empalme → el diálogo, el silencio del original y los subtítulos se colocan en la línea de tiempo.
8. **Editor** → **vista previa** 360p renderizada → **exportación** (marca + C2PA + SRT + JSON).

## 5. Estructura del proyecto

```
backend/
  app/
    main.py            API + estáticos + cabeceras de seguridad
    worker.py          trabajador independiente
    config.py          configuración por variables de entorno
    db.py              esquema SQLite y utilidades
    security.py        contraseñas PBKDF2, sesiones, URL firmadas HMAC
    policy.py          procedencia, declaración de uso, filtro de diálogos, consentimiento
    services.py        operaciones de dominio (versiones de vídeo, empalmes, revocación)
    storage.py         archivos en disco, tipos servibles
    api/               auth, projects, identities(+consentimiento público), dialogues, processing, files, system
    ai/                face_detect, quality, face_swap, lipsync, tts, asr
    media/             ffmpeg, watermark, subtitles, timeline, render, provenance
    jobs/              queue (cola persistente), tasks (trabajos), maintenance (limpieza)
  tests/               pruebas unitarias, de cola y de extremo a extremo
frontend/              PWA: index.html, css/, js/ (api, ui, components, history, screens/*), sw.js
deploy/                Dockerfile, docker-compose, Caddyfile
docs/                  esta documentación
```
