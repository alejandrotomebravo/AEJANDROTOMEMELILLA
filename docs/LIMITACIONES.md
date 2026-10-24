# Limitaciones conocidas y mejoras futuras

## Limitaciones actuales (honestas)

### Verificación
- **Claude y fal.ai no se han probado contra los servicios reales**: el entorno de desarrollo no tenía claves ni
  acceso de red a esos servicios. La integración sigue su documentación oficial y se prueba con dobles que
  reproducen las respuestas (`tests/test_gen.py`). Antes de producción, haga una generación real de cada tipo
  (retrato, guion, fotograma, clip) con un presupuesto pequeño.
- El **modo local** se ha probado de punta a punta (API y navegador con tamaño iPhone 13), pero **no es IA
  generativa**: filtros de imagen, composición y animática con movimiento de cámara.

### Calidad
- **Parecido de la cara**: depende del modelo de imagen. Con una sola foto frontal el parecido es menor; suba
  varias fotos (frontal y tres cuartos, buena luz). Las caricaturas exageran rasgos y pueden reconocerse peor.
- **Coherencia entre escenas**: cada fotograma se genera por separado a partir de los retratos; ropa, fondo o
  proporciones pueden variar ligeramente entre escenas. Regenere el fotograma antes de animar.
- **Clips de 5 o 10 s** (limitación de Kling 2.1): una escena de 7 s se genera de 5 s y una de 8 s, de 10 s.
- **Sin sincronización labial**: las voces se colocan sobre el clip, pero la boca del personaje no se mueve con el
  texto. (Mejora: modelo de *lip‑sync* sobre el clip generado o modelos de vídeo con audio.)
- Los modelos de vídeo pueden **deformar caras o manos** en movimientos rápidos; los filtros de seguridad del
  proveedor pueden rechazar una petición (la app muestra el error y no cobra el reintento automático).
- Las **voces** locales (eSpeak) suenan robóticas; Piper o ElevenLabs mejoran el resultado. Sin clonación de voz.
- La **reproducción del editor** en el navegador es aproximada; la exacta es la vista previa renderizada.
- Los clips se generan **en serie** (uno tras otro) para no disparar el gasto: un minuto de vídeo puede tardar 30‑50 min.

### Seguridad y cumplimiento
- La **autodeclaración** («soy yo») no es una verificación biométrica ni documental.
- El **consentimiento por enlace** acredita que alguien con acceso al enlace aceptó; no verifica su identidad real.
- El **filtro de instrucciones** es por patrones (y, con Claude, por el propio modelo): tiene falsos positivos y negativos.
- No hay detección automática de **personas famosas** en las fotos subidas: se basa en la declaración y el consentimiento.
- La **marca visible** puede recortarse con un editor externo tras la descarga; los metadatos y C2PA pueden eliminarse
  recodificando. No existe protección técnica absoluta.
- Sin cuotas de gasto por usuario (sólo confirmación previa de cada coste y registro del consumo).
- Sin limitación de intentos de inicio de sesión ni 2FA.

### Infraestructura
- SQLite: un único nodo (o NFS con baja concurrencia). Archivos en disco local; el cifrado en reposo depende del volumen.
- La base de datos de la v0.2 (intercambio de caras) **no se migra** al esquema v3: empiece con un `DATA_DIR` nuevo.

## Hoja de ruta

| Prioridad | Mejora |
|---|---|
| Alta | Prueba real con claves y ajuste de prompts; cuotas y presupuesto mensual por usuario |
| Alta | Sincronización labial sobre los clips generados; modelos de vídeo con audio nativo |
| Media | Personaje de referencia consistente (varias vistas del retrato) para mejorar la coherencia entre escenas |
| Media | Proveedor de vídeo autoalojado (Wan 2.2, Apache 2.0) para GPU propia |
| Media | Música generada o biblioteca con licencia; transiciones cruzadas entre escenas |
| Baja | PostgreSQL + S3, subidas reanudables, 2FA, firma electrónica del consentimiento |
