# Estudio IA de vídeo

Aplicación web instalable (PWA) **de uso propio y particular, gratuita y sin servicios de pago**, para crear vídeos a partir de
instrucciones escritas, con personajes que llevan **la cara o la caricatura** de personas que lo han autorizado
(usted mismo o terceros con consentimiento revocable) o personajes inventados, en el **estilo visual** que elija:
realista, caricatura, cómic, anime, animación 3D, acuarela, boceto, pixel art o plastilina.

Todo se procesa en su propio servidor: no hay claves de API, no hay costes por uso y no se envía nada a
servicios externos. Interfaz en español; funciona en iPhone (Safari / PWA), Android y ordenador.

## Flujo

| Paso | Qué hace | Cómo (gratuito, local) |
|---|---|---|
| Nuevo proyecto | Formato (9:16, 16:9, 1:1), estilo y declaración de uso responsable | — |
| 1 · Personajes | Persona autorizada (con sus fotos) o personaje inventado; retrato en el estilo elegido; voz | Recorte de la cara + filtro de estilo con OpenCV |
| 2 · Guion | Instrucciones → escenas con diálogos y descripciones; editable escena a escena | Por reglas: una escena por frase, diálogos entre «…» |
| 3 · Escenas | Fotograma de cada escena con los personajes → clip → voces y subtítulos | Composición con Pillow + animática FFmpeg (zoom/paneo) + eSpeak/Piper |
| 4 · Editor | Montaje multipista: orden, recortes, velocidad, color, fundidos, música, subtítulos, textos | FFmpeg |
| 5 · Vista previa | Render exacto en 360p con marca de agua | FFmpeg |
| 6 · Exportar | MP4 H.264/H.265 hasta 1080p (4K opcional), SRT, JSON de procedencia, C2PA si está instalado | FFmpeg |

> **Importante:** en esta versión las imágenes y los clips **no se crean con IA generativa**: son filtros,
> composiciones y animáticas con movimiento de cámara. La generación con modelos abiertos y gratuitos (sin
> pago por uso) necesita un ordenador con tarjeta gráfica NVIDIA; está prevista como siguiente fase
> ([LIMITACIONES](docs/LIMITACIONES.md#hoja-de-ruta)).

## Estado (v0.4)

| Área | Estado |
|---|---|
| Personajes con consentimiento, estilos, retratos | ✅ |
| Guion editable, fotogramas, clips, voces | ✅ Local y gratuito |
| Editor, marca de agua, exportación | ✅ |
| Seguridad y privacidad | ✅ Consentimiento revocable con borrado en cascada, menores bloqueados, filtros, **sin conexiones de salida** |
| Pruebas | ✅ 50 pruebas backend (incluida una que impide reintroducir servicios de pago) + 4 frontend; flujo completo en navegador con tamaño iPhone 13 |
| IA generativa con GPU propia | ⏳ Pendiente de conocer el equipo disponible |

## Inicio rápido

```bash
pip install -r backend/requirements-dev.txt
cp .env.example .env    # desarrollo: APP_ENV=development, DATA_DIR=./data
cd backend && uvicorn app.main:app --port 8000     # http://localhost:8000
```

Pruebas: `cd backend && python -m pytest -q` y `node --test frontend/tests.mjs`.

## Documentación
- [Prompt de especificación](docs/PROMPT.md)
- [Arquitectura](docs/ARQUITECTURA.md)
- [Instalación, ejecución y mantenimiento](docs/INSTALACION.md)
- [Despliegue](docs/DESPLIEGUE.md)
- [Costes](docs/COSTES.md)
- [Seguridad](docs/SEGURIDAD.md)
- [Privacidad y cumplimiento](docs/PRIVACIDAD_Y_CUMPLIMIENTO.md)
- [Limitaciones y hoja de ruta](docs/LIMITACIONES.md)
- API interactiva: `/api/docs`

## Aviso
Todas las imágenes, clips, vistas previas y exportaciones llevan el aviso visible **«VÍDEO GENERADO CON
INTELIGENCIA ARTIFICIAL»**, que no puede desactivarse; si hay personajes basados en personas reales se añade
obligatoriamente «PERSONAJES BASADOS EN PERSONAS REALES». El aviso **no sustituye** a las autorizaciones de imagen
y voz. No se permite usar la imagen de personas sin su consentimiento (incluidos famosos), de menores, ni crear
contenido sexual, suplantaciones, fraudes, acoso o desinformación que parezca real.
