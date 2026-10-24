# Arquitectura técnica (v0.3 · generación de vídeo)

> Estado a **24/10/2026**. Las licencias, precios y disponibilidad de servicios de terceros cambian con frecuencia:
> verifíquelos antes de un uso comercial.

## 1. Visión general

```
┌───────────────────────────┐      HTTPS       ┌─────────────────────────────────────────────┐
│ PWA (HTML + JS sin build) │ ───────────────▶ │ API FastAPI (Python 3.11+)                  │
│ iPhone · Android · PC     │ ◀─────────────── │  · auth, proyectos, personas, consentimiento │
│ personajes → guion →      │   URL firmadas   │  · reparto, guion, escenas (studio.py)       │
│ escenas → editor → export │  (sólo con marca)│  · políticas, costes, marca de agua          │
└───────────────────────────┘                  └───────────────┬─────────────────────────────┘
                                                               │ misma BD + volumen de datos
                                               ┌───────────────▼─────────────────────────────┐
                                               │ Trabajador(es)  python -m app.worker        │
                                               │  ┌───────────────┐  ┌────────────────────┐  │
                                               │  │ Guion: Claude │  │ Imagen y vídeo:    │  │
                                               │  │ (Anthropic) o │  │ fal.ai (cola REST) │  │
                                               │  │ básico local  │  │ o modo local       │  │
                                               │  └───────────────┘  └────────────────────┘  │
                                               │  FFmpeg · OpenCV · Pillow · TTS · ASR      │
                                               └─────────────────────────────────────────────┘
```

**Principio rector:** cada capacidad de IA se elige por configuración (`LLM_PROVIDER`, `IMAGE_PROVIDER`,
`VIDEO_PROVIDER`, con `auto` = usar el servicio si hay clave). La interfaz consulta `/api/system/capabilities` y
muestra qué es IA real y qué es modo local. **No se simula ninguna función.**

## 2. Decisiones y justificación

| Decisión | Alternativas evaluadas | Motivo |
|---|---|---|
| **FastAPI + Python** | Node/NestJS, Go | OpenCV, Pillow, Whisper y el SDK oficial de Anthropic son Python; los modelos de imagen/vídeo se consumen por HTTP. |
| **PWA sin paso de compilación** (ES modules) | React/Vite, app nativa | Instalable en iPhone desde Safari, acceso a galería (`<input type=file accept=video/*>`) y micrófono (MediaRecorder). Sin cadena de build que mantener. Una app nativa no aporta nada imprescindible en la Fase 2. |
| **SQLite (WAL) + cola propia persistente** | PostgreSQL + Redis/RQ/Celery | Un único nodo cubre el caso de uso inicial con cero servicios adicionales. La cola soporta reintentos, puntos de control, cancelación y recuperación ante caídas. El acceso está encapsulado en `db.py`/`jobs/queue.py` para migrar a PostgreSQL + Redis al escalar (Fase 4+). |
| **FFmpeg** por subproceso | PyAV, MoviePy | Estándar de facto, H.264/H.265, `drawtext`, `ass`, progreso fiable con `-progress`. Sin `shell=True` (seguridad). |
| **OpenCV** para validar fotos y filtros locales | MediaPipe | Licencia permisiva; comprueba que cada foto de una persona tiene una cara nítida; en modo local aplica los filtros de estilo. **OpenCV 5 eliminó Haar** → se fija `opencv-python-headless<5`. |
| **Marca de agua en el servidor** | Superposición en el cliente | Una marca aplicada en el navegador se elimina trivialmente. Se graba en los píxeles de *todas* las salidas (retratos, fotogramas, clips, vistas previas, exportaciones); las versiones sin marca son internas y nunca se sirven. |
| **Línea de tiempo con dos bases temporales** | Una sola base | Las voces y subtítulos de una escena se anclan a su clip y lo acompañan al reordenarlo; música y textos van en tiempo de salida. |
| **Generación en dos pasos: fotograma → clip** | Texto‑a‑vídeo directo | El fotograma (≈0,04 USD) fija caras, estilo y encuadre y se revisa antes de pagar el clip (≈0,25‑0,50 USD). Los modelos imagen‑a‑vídeo conservan mucho mejor la identidad que el texto‑a‑vídeo. |
| **Proveedores enchufables + modo local honesto** | Sólo nube | Sin claves la app funciona de punta a punta (pruebas, demos) y lo indica en pantalla: el modo local no se presenta como IA. |

## 3. Proveedores

### 3.1 Guion y prompts: Claude (`app/gen/llm.py`)
- SDK oficial `anthropic`, modelo configurable (`ANTHROPIC_MODEL`, por defecto `claude-opus-5-5`), salida **JSON
  estructurada** con esquema (`output_config.format = json_schema`), esfuerzo medio y *fallback* de servidor.
- Recibe las instrucciones y los **nombres y descripciones** del reparto; nunca las fotos.
- El sistema le pide rechazar (`refused`) contenido sexual, suplantaciones, desinformación verosímil, menores, etc.;
  la app trata `stop_reason = refusal` y `refused = true` como bloqueo de política.
- Escribe en español título, descripción, lugar, cámara y diálogos, y en inglés los prompts de fotograma y de movimiento.
- `normalize()` sanea la salida: duraciones 3‑10 s, sólo personajes del reparto, máx. 8 frases por escena, ids propios.
- Sin clave: **guion básico** por reglas (una escena por frase, diálogos entre comillas).

### 3.2 Imágenes y vídeo: fal.ai (`app/gen/fal.py`, `images.py`, `video.py`)
| Uso | Modelo por defecto | Entrada |
|---|---|---|
| Retrato de persona real | `fal-ai/nano-banana/edit` | Fotos de la persona (data URI) + estilo + descripción |
| Retrato de personaje ficticio | `fal-ai/nano-banana` | Descripción + estilo; se pide que no se parezca a nadie real |
| Fotograma clave de escena | `fal-ai/nano-banana/edit` | Retratos de los personajes de la escena + prompt + estilo + formato |
| Clip | `fal-ai/kling-video/v2.1/standard/image-to-video` | Fotograma + prompt de movimiento; 5 o 10 s |

- Cola REST (`POST https://queue.fal.run/{modelo}` → `status_url` → `response_url`) con `Authorization: Key …`.
- El identificador de la petición se guarda como **punto de control**: si el trabajador se reinicia, se reanuda el
  sondeo en vez de volver a pagar el envío.
- Las descargas de resultados pasan por `app/net.py` (anti‑SSRF, límites de tamaño) y se valida que sean imagen/vídeo.
- Las condiciones de fal.ai exigen **consentimiento escrito** para usar la imagen de una persona: la app sólo envía
  fotos de identidades con consentimiento vigente (autodeclaración o enlace firmado).
- Modo local: recorte de la cara + filtro OpenCV del estilo (retratos), composición con Pillow (fotogramas) y
  animática FFmpeg con zoom/paneo según la cámara indicada (clips).

### 3.3 Voz, subtítulos y procedencia
- Voces: eSpeak NG (GPL‑3.0), Piper (motor GPL‑3.0; cada voz con su licencia) o ElevenLabs (de pago, voces de catálogo).
  Sin clonación de voz. Cada personaje tiene su voz; las frases se colocan sobre el clip de su escena con subtítulos.
- ASR opcional: `faster-whisper` (MIT).
- Procedencia: etiquetas MP4, JSON de procedencia (proveedores, modelo del guion, personajes reales sí/no) y, si
  está `c2patool`, manifiesto C2PA con `digitalSourceType = trainedAlgorithmicMedia`.

## 4. Flujo de datos de un proyecto

1. **Proyecto**: formato, estilo y declaración de uso (finalidad + compromisos obligatorios, en `audit`).
2. **Personas** (pantalla «Personas»): autodeclaración o **enlace de consentimiento** caducable y revocable; fotos
   validadas (una cara nítida). Menores bloqueados por defecto.
3. **Reparto** (`cast_members`): persona autorizada o ficticio, descripción, estilo, voz → trabajo `portrait`.
4. **Guion**: `brief` → trabajo `storyboard` (o `rewrite_scene`) → `projects.storyboard` (JSON editable con `PUT`).
5. **Escenas**: trabajo `keyframe` → imagen interna + copia con aviso; trabajo `animate` → clip (fuente `generated`),
   copia de revisión 480p con marca, colocación automática en la línea de tiempo en el orden del guion; trabajo `tts`
   por frase → voz y subtítulos anclados al clip.
6. **Editor** → **vista previa** 360p → **exportación** (marca + C2PA + SRT + JSON).
7. **Revocación**: borra fotos, retratos, fotogramas y clips con esa persona, vistas previas y exportaciones del proyecto.

## 5. Estructura del proyecto

```
backend/app/
  main.py, worker.py, config.py, db.py (esquema v3), security.py, policy.py, services.py, storage.py, net.py
  api/      auth, projects, studio (reparto, guion, escenas, estimación), identities, dialogues, processing, files, system
  gen/      styles, llm (Claude / básico), fal (cola REST), images (retratos, fotogramas), video (clips)
  ai/       face_detect, quality (fotos), tts, asr
  media/    ffmpeg, watermark, subtitles, timeline, render, provenance
  jobs/     queue (cola persistente), tasks, maintenance
backend/tests/   unidades, cola, generador (dobles de Claude y fal.ai), flujo completo con FFmpeg real
frontend/        PWA: js/screens/{personajes,guion,escenas,editor,vista,exportar,...}.js, sw.js
deploy/          Dockerfile, docker-compose, Caddyfile
```
