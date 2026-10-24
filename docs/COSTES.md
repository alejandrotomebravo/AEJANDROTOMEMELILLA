# Costes

> Estado a **24/10/2026**. La aplicación es **gratuita por uso**: no integra ningún servicio de pago (ni de
> guion, ni de imagen, ni de vídeo, ni de voz) y no necesita claves de API. Una prueba automática
> (`test_app_is_free_no_paid_integrations`) impide que se vuelvan a introducir.

## 1. Qué cuesta realmente

| Concepto | Coste por uso | Coste fijo |
|---|---|---|
| Guion, retratos, fotogramas, clips | 0 € | — |
| Voces (eSpeak NG, Piper) | 0 € | — |
| Render y exportación (FFmpeg) | 0 € | — |
| Servidor | — | El de la máquina donde se instale: un ordenador propio (sólo electricidad) o un servidor alquilado |
| Dominio y HTTPS (opcional, para usarlo desde el iPhone fuera de casa) | — | Dominio ≈ 10 €/año; certificado gratuito (Let's Encrypt vía Caddy) |

## 2. Tiempo de procesamiento (medido en un servidor de 4 núcleos, sin GPU)

| Paso | Medido | Por minuto de vídeo (12 escenas de 5 s) |
|---|---|---|
| Retrato | ≈ 0,7 s | (una vez por personaje) |
| Fotograma | < 0,1 s | ≈ 1 s |
| Clip de 5 s (animática 720p) | ≈ 1,1 s | ≈ 15 s |
| Exportación 1080p H.264 | ≈ 1,6 × la duración | ≈ 1,5‑2 min |
| **Total** | | **≈ 2‑3 min** |

## 3. Fase siguiente: IA generativa gratuita con GPU propia (no implementada)

Con un ordenador con tarjeta gráfica NVIDIA se pueden ejecutar modelos abiertos sin pagar por uso. El coste pasa
a ser la compra/amortización del equipo y la electricidad:

| Equipo | Qué permitiría | Electricidad aprox. por minuto de vídeo |
|---|---|---|
| Sin GPU | Lo actual (sin IA generativa) | Despreciable |
| NVIDIA con 12‑16 GB | Guion con un modelo local; retratos y fotogramas con IA; vídeo corto y lento | Céntimos (≈ 0,3 kW × 1‑2 h × precio del kWh) |
| NVIDIA con 24 GB o más | Lo anterior + vídeo imagen→vídeo (p. ej. Wan 2.2, Apache 2.0) a velocidad razonable | Céntimos‑decenas de céntimos |

Fórmula: `coste = potencia (kW) × horas de cálculo × precio del kWh`. Los tiempos dependen del modelo y la GPU y
se medirán con el equipo real antes de darlos por buenos.
