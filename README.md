# Estudio IA de vídeo

Aplicación web instalable (PWA) para **generar vídeos con IA a partir de instrucciones escritas**, con
personajes que llevan **la cara o la caricatura** de personas que lo han autorizado (usted mismo o terceros
con consentimiento revocable) o personajes inventados, en el **estilo visual** que elija:
realista, caricatura, cómic, anime, animación 3D, acuarela, boceto, pixel art o plastilina.

Interfaz en español; funciona en iPhone (Safari / PWA), Android y ordenador.

## Flujo

| Paso | Qué hace | Proveedor con IA | Modo local gratuito (sin claves) |
|---|---|---|---|
| Nuevo proyecto | Formato (9:16, 16:9, 1:1), estilo y declaración de uso responsable | — | — |
| 1 · Personajes | Reparto: persona real autorizada (con sus fotos) o personaje inventado; retrato en el estilo elegido; voz | fal.ai · `nano-banana/edit` (con la foto de referencia) | Filtro OpenCV del estilo sobre la foto / figura dibujada |
| 2 · Guion | Instrucciones → guion gráfico con escenas, diálogos y prompts de imagen y vídeo; editable y reescribible por escena | Claude (API de Anthropic) con salida JSON estructurada | Una escena por frase; diálogos entre «…» |
| 3 · Escenas | Fotograma clave con los personajes → clip de vídeo → voces y subtítulos | fal.ai · `nano-banana/edit` + Kling 2.1 imagen‑a‑vídeo (5/10 s) | Composición PIL + animática FFmpeg (zoom/paneo) |
| 4 · Editor | Montaje multipista: orden, recortes, velocidad, color, fundidos, música, subtítulos, textos | — | — |
| 5 · Vista previa | Render exacto en 360p con marca de agua | — | — |
| 6 · Exportar | MP4 H.264/H.265 hasta 1080p (4K opcional), SRT, JSON de procedencia, C2PA si está instalado | — | — |

Cada generación de pago muestra antes su **coste estimado** y exige confirmación (HTTP 402 `confirmar_coste`).
El modo local **no es IA generativa** y la interfaz lo dice expresamente: sirve para probar el flujo.

## Estado (v0.3)

| Fase | Contenido | Estado |
|---|---|---|
| 1 | Viabilidad, proveedores y costes | ✅ [ARQUITECTURA](docs/ARQUITECTURA.md), [COSTES](docs/COSTES.md), [PROMPT](docs/PROMPT.md) |
| 2 | Personajes con consentimiento, estilos, retratos | ✅ |
| 3 | Guion con Claude, fotogramas y clips con fal.ai, voces | ✅ Integración implementada; **no probada contra los servicios reales** (sin claves ni red en el entorno de desarrollo; probada con dobles que siguen la documentación oficial) |
| 4 | Editor, marca de agua, exportación | ✅ |
| 5 | Seguridad y privacidad | ✅ Consentimiento revocable con borrado en cascada, menores bloqueados, filtros de instrucciones, red de salida anti‑SSRF |
| 6 | Pruebas, instalación, despliegue | ✅ 89 pruebas backend + 4 frontend; flujo completo probado en navegador con tamaño iPhone 13 (modo local) |

## Inicio rápido

```bash
pip install -r backend/requirements-dev.txt
cp .env.example .env    # desarrollo: APP_ENV=development, DATA_DIR=./data
# opcional: ANTHROPIC_API_KEY=… y FAL_KEY=… para generar con IA
cd backend && uvicorn app.main:app --port 8000     # http://localhost:8000
```

Pruebas: `cd backend && python -m pytest -q` y `node --test frontend/tests.mjs`.

## Documentación
- [Prompt de especificación (v0.3)](docs/PROMPT.md)
- [Arquitectura y justificación](docs/ARQUITECTURA.md)
- [Instalación, ejecución y mantenimiento](docs/INSTALACION.md)
- [Despliegue](docs/DESPLIEGUE.md)
- [Costes por minuto](docs/COSTES.md)
- [Seguridad](docs/SEGURIDAD.md)
- [Privacidad y cumplimiento](docs/PRIVACIDAD_Y_CUMPLIMIENTO.md)
- [Limitaciones y mejoras](docs/LIMITACIONES.md)
- API interactiva: `/api/docs`

## Aviso
Todas las imágenes, clips, vistas previas y exportaciones llevan el aviso visible **«VÍDEO GENERADO CON
INTELIGENCIA ARTIFICIAL»**, que no puede desactivarse; si hay personajes basados en personas reales se añade
obligatoriamente «PERSONAJES BASADOS EN PERSONAS REALES». El aviso **no sustituye** a las autorizaciones de imagen
y voz. No se permite usar la imagen de personas sin su consentimiento (incluidos famosos), de menores, ni crear
contenido sexual, suplantaciones, fraudes, acoso o desinformación que parezca real.
