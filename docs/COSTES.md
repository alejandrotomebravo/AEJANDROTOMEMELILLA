# Estimación de costes por minuto de vídeo generado

> Cifras orientativas a **24/10/2026**. Los precios de los proveedores cambian con frecuencia: los importes que
> usa la app son **parámetros configurables** (`FAL_IMAGE_COST`, `FAL_VIDEO_COST_PER_SECOND`) con valores por
> defecto aproximados. Verifíquelos en https://fal.ai/pricing y https://www.anthropic.com/pricing antes de
> presupuestar. La factura real es siempre la del proveedor.

## 1. Unidades que se generan

| Elemento | Cuántos | Proveedor | Precio unitario por defecto (configurable) |
|---|---|---|---|
| Retrato de personaje | 1 por personaje (y por cada regeneración) | fal.ai `nano-banana/edit` o `nano-banana` | 0,04 USD/imagen |
| Fotograma clave | 1 por escena | fal.ai `nano-banana/edit` | 0,04 USD/imagen |
| Clip de vídeo | 1 por escena (5 o 10 s) | fal.ai Kling 2.1 Standard imagen‑a‑vídeo | 0,05 USD/s |
| Guion + prompts | 1 por guion (y por escena reescrita) | Claude (Anthropic) | ≈ 0,05 USD por guion (estimación: unos miles de tokens; depende del modelo y de la longitud) |
| Voces | 1 por frase | eSpeak NG / Piper (local) · ElevenLabs (opcional) | 0 en local; ElevenLabs según `ELEVENLABS_COST_PER_1K_CHARS` |
| Render y exportación | — | FFmpeg local | 0 (paga la máquina) |

## 2. Cálculo paso a paso: 1 minuto de vídeo

Supuestos: 12 escenas de 5 s, 2 personajes, una sola generación de cada elemento.

| Paso | Operación | Importe (USD) |
|---|---|---|
| Retratos | 2 × 0,04 | 0,08 |
| Guion | 1 × 0,05 | 0,05 |
| Fotogramas clave | 12 × 0,04 | 0,48 |
| Clips de vídeo | 12 × 5 s × 0,05 | 3,00 |
| **Total, sin repeticiones** | | **3,61** |
| Total realista (cada fotograma y clip se genera ≈ 2 veces hasta que convence) | 0,08 + 0,05 + 2 × (0,48 + 3,00) | **≈ 7,09** |

El **vídeo supone ≈ 83 %** del coste: revise y regenere el fotograma (barato) antes de animar el clip (caro).

## 3. Comparativa de modos

| Modo | Coste por minuto | Calidad | Tiempo de espera aprox. por minuto |
|---|---|---|---|
| Local gratuito (sin claves) | 0 USD | Animática: filtros y movimiento de cámara, **sin IA generativa** | < 2 min en CPU |
| Claude + fal.ai (valores por defecto) | ≈ 3,6 – 7,1 USD | Escenas generadas con IA con la cara/caricatura de los personajes | ≈ 30‑50 min (12 clips en cola, ~2‑4 min cada uno; la app los procesa en serie) |
| Clips de 10 s en lugar de 5 s | Igual coste por segundo; la mitad de fotogramas | Menos cortes, más riesgo de deriva en el clip | Similar |

## 4. Alternativas autoalojadas (no integradas)

Modelos abiertos de imagen‑a‑vídeo como **Wan 2.2** (Apache 2.0) pueden ejecutarse en una GPU propia (≈ 24 GB de
VRAM para la variante pequeña): el coste marginal pasa a ser la hora de GPU (`coste = horas × precio_hora`). No están
integrados en esta versión: el proveedor de vídeo es intercambiable (`app/gen/video.py`) para añadirlos.

## 5. Servicios descartados o retirados
- **OpenAI Sora (API)**: retirada el 24/09/2026.
- **Google Veo 3.1 (vistas previas)**: retiradas el 22/10/2026 en favor de nuevos modelos; ⚠ cambio muy reciente,
  verifique la oferta actual de Google antes de integrarlo.
