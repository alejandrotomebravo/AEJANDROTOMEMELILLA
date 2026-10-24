# Arquitectura técnica (v0.4 · gratuita y local)

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
                                               │  │ Guion por     │  │ Imagen: OpenCV +   │  │
                                               │  │ reglas        │  │ Pillow · Vídeo:    │  │
                                               │  │               │  │ animática FFmpeg   │  │
                                               │  └───────────────┘  └────────────────────┘  │
                                               │  FFmpeg · OpenCV · Pillow · TTS · ASR      │
                                               └─────────────────────────────────────────────┘
```

**Principio rector:** todo se ejecuta en el propio servidor, sin servicios de pago ni conexiones de salida.
Guion (`gen/llm.py`), imágenes (`gen/images.py`) y vídeo (`gen/video.py`) son módulos intercambiables para poder
conectar más adelante modelos abiertos en una GPU propia sin tocar la API ni la interfaz. La interfaz consulta
`/api/system/capabilities` y dice con claridad qué hace cada paso. **No se simula ninguna función.**

## 2. Decisiones y justificación

| Decisión | Alternativas evaluadas | Motivo |
|---|---|---|
| **FastAPI + Python** | Node/NestJS, Go | OpenCV, Pillow, Whisper y los modelos abiertos de imagen/vídeo (fase con GPU) son Python. |
| **PWA sin paso de compilación** (ES modules) | React/Vite, app nativa | Instalable en iPhone desde Safari, acceso a galería (`<input type=file accept=video/*>`) y micrófono (MediaRecorder). Sin cadena de build que mantener. Una app nativa no aporta nada imprescindible en la Fase 2. |
| **SQLite (WAL) + cola propia persistente** | PostgreSQL + Redis/RQ/Celery | Un único nodo cubre el caso de uso inicial con cero servicios adicionales. La cola soporta reintentos, puntos de control, cancelación y recuperación ante caídas. El acceso está encapsulado en `db.py`/`jobs/queue.py` para migrar a PostgreSQL + Redis al escalar (Fase 4+). |
| **FFmpeg** por subproceso | PyAV, MoviePy | Estándar de facto, H.264/H.265, `drawtext`, `ass`, progreso fiable con `-progress`. Sin `shell=True` (seguridad). |
| **OpenCV** para validar fotos y filtros locales | MediaPipe | Licencia permisiva; comprueba que cada foto de una persona tiene una cara nítida; en modo local aplica los filtros de estilo. **OpenCV 5 eliminó Haar** → se fija `opencv-python-headless<5`. |
| **Marca de agua en el servidor** | Superposición en el cliente | Una marca aplicada en el navegador se elimina trivialmente. Se graba en los píxeles de *todas* las salidas (retratos, fotogramas, clips, vistas previas, exportaciones); las versiones sin marca son internas y nunca se sirven. |
| **Línea de tiempo con dos bases temporales** | Una sola base | Las voces y subtítulos de una escena se anclan a su clip y lo acompañan al reordenarlo; música y textos van en tiempo de salida. |
| **Generación en dos pasos: fotograma → clip** | Texto‑a‑vídeo directo | El fotograma fija caras, estilo y encuadre y se revisa antes de animarlo. Con modelos imagen‑a‑vídeo (fase con GPU) la identidad se conserva mucho mejor que con texto‑a‑vídeo. |
| **Gratuita y sin red de salida** | Servicios en la nube de pago | Decisión del propietario: coste por uso cero y ningún dato sale del servidor. La interfaz no presenta los filtros y animáticas como IA generativa. |

## 3. Procesamiento

### 3.1 Guion (`app/gen/llm.py`)
Por reglas: una escena por frase, personajes detectados por su nombre, diálogos entre comillas «…», cámara
alternada y duración repartida si se indica una duración total. `normalize()` valida también el guion editado a
mano (duraciones 3‑10 s, sólo personajes del reparto, máx. 8 frases por escena).

### 3.2 Imágenes y vídeo (`app/gen/images.py`, `app/gen/video.py`)
| Uso | Técnica |
|---|---|
| Retrato de persona real | Recorte de la cara (detección OpenCV) + filtro del estilo (cómic, anime, boceto, acuarela, pixel art, realce) |
| Personaje inventado | Figura dibujada con su nombre y colores propios |
| Fotograma de escena | Fondo, título y descripción + retratos de los personajes presentes (Pillow) |
| Clip | Animática FFmpeg: acercamiento, alejamiento o paneo según la cámara de la escena (3‑10 s) |

### 3.3 Voz, subtítulos y procedencia
- Voces: eSpeak NG (GPL‑3.0) o Piper (motor GPL‑3.0; cada voz con su licencia), ambas locales y gratuitas.
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
  main.py, worker.py, config.py, db.py (esquema v3), security.py, policy.py, services.py, storage.py
  api/      auth, projects, studio (reparto, guion, escenas, estimación), identities, dialogues, processing, files, system
  gen/      styles, llm (guion por reglas), images (retratos, fotogramas), video (animática)
  ai/       face_detect, quality (fotos), tts, asr
  media/    ffmpeg, watermark, subtitles, timeline, render, provenance
  jobs/     queue (cola persistente), tasks, maintenance
backend/tests/   unidades, cola, generador (incl. garantía de gratuidad), flujo completo con FFmpeg real
frontend/        PWA: js/screens/{personajes,guion,escenas,editor,vista,exportar,...}.js, sw.js
deploy/          Dockerfile, docker-compose, Caddyfile
```
