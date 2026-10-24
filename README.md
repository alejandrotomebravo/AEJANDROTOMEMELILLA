# Estudio IA de vídeo

Aplicación web instalable (PWA) para crear vídeos personalizados con IA de forma responsable:
**sustitución facial con rostros autorizados, diálogos (texto a voz, micrófono o archivo), sincronización
labial, editor multipista y exportación con marca de agua obligatoria de contenido sintético.**

Toda la interfaz está en español y funciona en iPhone (Safari / PWA), Android y ordenador.

## Estado por fases

| Fase | Contenido | Estado |
|---|---|---|
| 1 | Viabilidad, arquitectura, licencias, costes | ✅ [docs/ARQUITECTURA.md](docs/ARQUITECTURA.md), [docs/COSTES.md](docs/COSTES.md) |
| 2 | Importar → rostro autorizado → diálogo → voz/audio → lipsync → exportar con marca | ✅ Funcional de extremo a extremo |
| 3 | Editor multipista, subtítulos, voces, ajustes avanzados | ✅ Básico: pistas de vídeo/diálogo/audio/música/subtítulos/gráficos, deshacer/rehacer, autoguardado, comparación. Ver mejoras en [LIMITACIONES](docs/LIMITACIONES.md) |
| 4 | Rendimiento, seguridad, almacenamiento, despliegue | 🟡 Procesamiento por fragmentos, reintentos, recuperación, URL firmadas, Docker + Caddy. Pendiente: PostgreSQL/S3, cuotas, 2FA |
| 5 | Pruebas y documentación | ✅ 40 pruebas backend + 2 frontend; prueba de humo en navegador (iPhone 13) |

### Qué funciona sin configurar nada
Importación (MP4, MOV, iPhone HEVC con rotación), análisis de rostros (OpenCV Haar), detección de actividad de audio
y escenas, identidades y consentimiento por enlace, voces eSpeak NG (si está instalado), grabación con micrófono,
importación de audio, editor, vista previa y exportación H.264/H.265 con marca de agua, SRT y JSON de procedencia.

### Qué requiere configuración (no se simula)
| Módulo | Requiere |
|---|---|
| Sustitución facial | `insightface` + modelos `buffalo_l` e `inswapper_128.onnx` (**uso no comercial salvo licencia**) |
| Sincronización labial | Clave de **sync.so** (de pago, ≈2,4‑3 USD/min) **o** un modelo local con GPU (Wav2Lip/LatentSync) |
| Voces naturales | Piper (+ voces) o clave de ElevenLabs |
| Subtítulos automáticos | `faster-whisper` |
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
- [Costes por minuto](docs/COSTES.md)
- [Privacidad y cumplimiento](docs/PRIVACIDAD_Y_CUMPLIMIENTO.md)
- [Limitaciones y mejoras](docs/LIMITACIONES.md)
- API interactiva: `/api/docs`

## Aviso
La marca de agua «VÍDEO EDITADO CON INTELIGENCIA ARTIFICIAL» no puede desactivarse y **no sustituye** a las
autorizaciones de derechos de autor, imagen o voz que sean necesarias. La aplicación no permite contenido
íntimo, suplantaciones fraudulentas, acoso ni falsificación de pruebas, y bloquea el uso de imagen y voz de menores
por defecto.
