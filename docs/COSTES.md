# Estimación de costes por minuto de vídeo procesado

> Cifras orientativas a 24/10/2026. Los precios de los proveedores **cambian con frecuencia y las fuentes
> consultadas no siempre coinciden**: verifíquelos en sus páginas oficiales antes de presupuestar. Los costes
> de servidor dependen de su proveedor; aquí se dan **fórmulas** y ejemplos con precios *supuestos* (marcados).

## 1. Qué se procesa realmente

- **Sustitución facial**: sólo el tramo en que aparece el personaje (no el vídeo entero).
- **Sincronización labial**: sólo la duración de cada diálogo (p. ej., 15 s de diálogo en un vídeo de 2 min).
- **Render final**: el montaje completo.

## 2. Procesamiento local (gratuito por uso; paga la infraestructura)

| Etapa | Factor de tiempo (configurable) | Tiempo por minuto de vídeo |
|---|---|---|
| Importación + análisis (CPU) | ≈ 0,2‑0,5× | 15‑30 s |
| Face swap InsightFace · CPU | `FACESWAP_SECONDS_PER_SECOND_CPU` ≈ 12× | ≈ 12 min |
| Face swap InsightFace · GPU (clase T4/L4) | ≈ 1‑2× | 1‑2 min |
| Lipsync local (Wav2Lip/LatentSync) · GPU | 5‑20× según modelo | 5‑20 min |
| Render 1080p H.264 · CPU 4 núcleos | ≈ 1,6× | ≈ 1,5‑2 min |
| Render 1080p H.265 | ≈ 3,5× | ≈ 3‑4 min |

**Fórmula:** `coste = (segundos de procesamiento / 3600) × precio_hora_máquina`.

*Ejemplo con precio supuesto* de 0,60 €/h para una GPU en la nube: face swap de 1 min (≈ 1,5 min GPU)
≈ 0,015 €; lipsync local de 1 min (≈ 10 min GPU) ≈ 0,10 €. Con GPU propia, el coste marginal es la electricidad.

## 3. Procesamiento remoto de pago

| Servicio | Tarifa publicada (fuente: documentación de sync.so) | Por minuto |
|---|---|---|
| sync.so `lipsync-2` | 0,04‑0,05 USD/s según plan | **2,40‑3,00 USD** |
| sync.so `lipsync-2-pro` | 0,067‑0,083 USD/s | 4,00‑5,00 USD |
| sync.so `lipsync-1.9.0-beta` | 0,02‑0,025 USD/s | 1,20‑1,50 USD |
| ElevenLabs TTS | por caracteres según plan (`ELEVENLABS_COST_PER_1K_CHARS`, por defecto **supuesto** 0,30 USD/1000) | ≈ 900 caracteres por minuto hablado → ≈ 0,27 USD (*supuesto*) |

La aplicación muestra el coste estimado **antes** de cada trabajo remoto, exige confirmación y registra el
consumo en **Ajustes → Consumo**.

## 3b. Importación desde Internet

Wikimedia Commons, Internet Archive y enlaces directos: sin coste de API (sólo transferencia y disco). Pexels: API
gratuita con límites de uso. YouTube Data API: gratuita con cuota diaria (cada búsqueda consume 100 unidades de la cuota
estándar; compruebe su cuota en Google Cloud). Vimeo: requiere un plan del propietario que permita descargas.

## 4. Ejemplo completo

Vídeo vertical de 60 s, un personaje visible 40 s, dos diálogos que suman 20 s, export 1080p H.264:

| Concepto | Local CPU | Local GPU | Lipsync remoto (sync.so lipsync‑2) |
|---|---|---|---|
| Face swap 40 s | ≈ 8 min | ≈ 1 min | (local) |
| Voz 20 s | eSpeak/Piper: 0 € | 0 € | ElevenLabs ≈ 0,09 USD (*supuesto*) |
| Lipsync 20 s | no viable en CPU | ≈ 3‑7 min | ≈ 0,80‑1,00 USD, ≈ 1 min |
| Render 60 s | ≈ 1,5 min | ≈ 1,5 min | ≈ 1,5 min |
| **Total** | — | **≈ 6‑10 min, coste de máquina** | **≈ 1 USD + máquina** |
