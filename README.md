# Estudio IA de vídeo

Aplicación web instalable (PWA) para crear vídeos personalizados con IA de forma responsable:
**importación de vídeos de fuentes autorizadas de Internet o del móvil/ordenador, selección de escenas,
sustitución facial con rostros autorizados, diálogos (texto a voz, micrófono o archivo), sincronización
labial, editor multipista y exportación con marca de agua obligatoria de contenido sintético.**

Toda la interfaz está en español y funciona en iPhone (Safari / PWA), Android y ordenador.

## Estado por fases (v0.2)

| Fase | Contenido | Estado |
|---|---|---|
| 1 | Viabilidad, tecnologías, fuentes de vídeo realmente importables | ✅ [ARQUITECTURA](docs/ARQUITECTURA.md), [CONECTORES](docs/CONECTORES.md), [COSTES](docs/COSTES.md) |
| 2 | Archivos locales (por lotes) + fuentes de Internet compatibles con sus condiciones | ✅ Wikimedia Commons, Internet Archive, Pexels (con clave), Vimeo (sólo con permiso de descarga), enlace directo. YouTube: sólo búsqueda/metadatos |
| 3 | Detección facial, sustitución autorizada (con intensidad), diálogos, sincronización labial | ✅ Integración real; modelos según configuración (ver abajo) |
| 4 | Editor multipista, marca de agua, exportación | ✅ Varias fuentes, velocidad, fundidos, color, volumen, subtítulos, gráficos, créditos, deshacer, autoguardado, comparación |
| 5 | Más conectores, rendimiento y seguridad | 🟡 Anti‑SSRF, firmas de archivo, FFmpeg aislado, conectores extensibles. Pendiente: OAuth de nubes, PostgreSQL/S3, cuotas |
| 6 | Pruebas, instalación, despliegue | ✅ 91 pruebas backend + 4 frontend; prueba en navegador con tamaño iPhone |

### Qué funciona sin configurar nada
Importación de archivos (MP4, MOV, iPhone HEVC con rotación, por lotes, por fragmentos), importación desde **Wikimedia
Commons**, **Internet Archive** y **enlaces directos**, fotogramas de previsualización, detección de escenas, rostros
(OpenCV) y audio, identidades con consentimiento por enlace, voces eSpeak NG (si está instalado), micrófono, editor,
vista previa y exportación H.264/H.265 con marca de agua, créditos, SRT y JSON de procedencia.

> ⚠ Los conectores de Internet se han probado con respuestas que siguen la documentación oficial y con descargas
> reales desde un servidor local: el entorno de desarrollo no tenía acceso a esas plataformas. Verifíquelos contra los
> servicios reales antes de producción ([CONECTORES §4](docs/CONECTORES.md)).

### Qué requiere configuración (no se simula)
| Módulo | Requiere |
|---|---|
| Sustitución facial | `insightface` + modelos `buffalo_l` e `inswapper_128.onnx` (**uso no comercial salvo licencia**) |
| Sincronización labial | Clave de **sync.so** (de pago, ≈2,4‑3 USD/min) **o** un modelo local con GPU (Wav2Lip/LatentSync) |
| Voces naturales | Piper (+ voces) o clave de ElevenLabs |
| Subtítulos automáticos (voz a texto) | `faster-whisper` |
| Pexels / Vimeo / búsqueda en YouTube | `PEXELS_API_KEY` / `VIMEO_ACCESS_TOKEN` / `YOUTUBE_API_KEY` |
| C2PA firmado | `c2patool` + certificado |

## Inicio rápido

```bash
pip install -r backend/requirements-dev.txt
cp .env.example .env    # para desarrollo: APP_ENV=development, DATA_DIR=./data
cd backend && uvicorn app.main:app --port 8000
# http://localhost:8000
```

Pruebas: `cd backend && python -m pytest -q` y `node --test frontend/tests.mjs`.

## Documentación
- [Arquitectura y justificación](docs/ARQUITECTURA.md)
- [Instalación, ejecución y mantenimiento](docs/INSTALACION.md)
- [Despliegue](docs/DESPLIEGUE.md)
- [Fuentes de Internet y cómo añadir conectores](docs/CONECTORES.md)
- [Seguridad del extractor](docs/SEGURIDAD.md)
- [Costes por minuto](docs/COSTES.md)
- [Privacidad y cumplimiento](docs/PRIVACIDAD_Y_CUMPLIMIENTO.md)
- [Limitaciones y mejoras](docs/LIMITACIONES.md)
- API interactiva: `/api/docs`

## Aviso
La marca de agua «VÍDEO EDITADO CON INTELIGENCIA ARTIFICIAL» no puede desactivarse y **no sustituye** a las
autorizaciones de derechos de autor, imagen o voz que sean necesarias. La aplicación no permite contenido
íntimo, suplantaciones fraudulentas, acoso ni falsificación de pruebas, y bloquea el uso de imagen y voz de menores
por defecto.
