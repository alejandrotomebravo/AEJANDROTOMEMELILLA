# Limitaciones conocidas y mejoras futuras

## Limitaciones actuales (honestas)

### Calidad y tecnología
- **No hay sincronización perfecta ni procesamiento 4K en tiempo real.** Todo el procesamiento es por lotes. 4K está desactivado por defecto (`ALLOW_4K`).
- La **calidad del face swap** depende de InsightFace `inswapper_128` (resolución interna 128 px): en planos muy cercanos puede verse suave; oclusiones (manos, gafas), perfiles extremos y cambios fuertes de luz producen artefactos.
- La **detección por defecto (Haar)** es básica: no detecta perfiles bien ni da puntos faciales. Con YuNet mejora y se estima el giro de la cabeza.
- El **seguimiento** agrupa caras por posición entre muestras cada 0,5 s; si dos personas se cruzan puede intercambiar etiquetas (se avisa en el informe de calidad cuando hay solapamiento).
- La **evaluación de calidad** previa al lipsync es heurística (tamaño, visibilidad, giro, luz, movimiento). La comprobación posterior sólo verifica duración y presencia del rostro; **no mide la sincronía** (requeriría SyncNet, mejora futura).
- La **detección de diálogos** del vídeo original es por energía de audio: no distingue voz de música.
- Las **expresiones** (Paso 5) se conservan según lo que haga el modelo; la app no ofrece control directo de expresiones faciales.
- La **reproducción del editor** en el navegador es aproximada; la exacta es la vista previa renderizada.
- **Sin clonación de voz** (decisión deliberada en esta fase).
- Subidas en una sola petición: un corte de red obliga a reintentar la subida completa (falta subida reanudable).
- Importación desde **almacenamiento en la nube**: en iPhone funciona a través del selector de Archivos (iCloud Drive, Google Drive, Dropbox si están instalados); no hay integración directa por OAuth.
- **Conectores de Internet** probados con respuestas simuladas según la documentación oficial y descargas locales, no contra los servicios reales (sin acceso desde el entorno de desarrollo).
- **YouTube** no es importable (condiciones de servicio). **Vimeo** sólo si el token tiene permiso de descarga. **Vimeo** no ofrece búsqueda en esta versión.
- La licencia de Internet Archive no siempre consta; la de Commons puede ser múltiple (la API la marca como poco fiable en ese caso).
- Las **transiciones** son fundidos de entrada/salida por escena (a negro y en audio); no hay fundidos cruzados entre escenas.
- La **velocidad** de escena se aplica a imagen y audio (con `atempo`, sin cambio de tono); la reproducción en el navegador es aproximada.
- La **intensidad** de la sustitución facial mezcla el rostro generado con el original: valores bajos pueden producir un efecto de «doble imagen» en movimientos rápidos.

### Seguridad y cumplimiento
- La **autodeclaración** («soy yo») no es una verificación biométrica ni documental de identidad.
- El **consentimiento por enlace** acredita que alguien con acceso al enlace aceptó; no verifica la identidad real de quien lo abre (mejora: firma electrónica cualificada o verificación con documento).
- La **verificación de procedencia** es documental: comprueba licencias CC por su URL, pero no puede verificar que el usuario diga la verdad.
- El **filtro de diálogos** es por patrones: tiene falsos positivos y negativos.
- No hay **detección de personas públicas** ni de contenido sexual en la imagen.
- La **marca de agua visible** se puede recortar con un editor externo tras la descarga; por eso se añaden metadatos y, si se configura, C2PA (que también puede eliminarse re-codificando). No existe protección técnica absoluta.
- **C2PA** sin certificado propio usa el certificado de pruebas de c2patool (no confiable para verificadores).
- Sin cuotas de gasto por usuario (sólo confirmación de coste y registro).
- Sin limitación de intentos de inicio de sesión ni 2FA.

### Infraestructura
- SQLite: un único nodo (o NFS con baja concurrencia).
- Archivos en disco local; el cifrado en reposo depende del volumen.

## Hoja de ruta

| Fase | Mejora |
|---|---|
| 3 | Fundidos cruzados entre escenas, estilos de subtítulos, más proveedores de voz (Azure, Google) con selector de idioma, edición de expresiones si el modelo lo permite |
| 3 | Métrica de sincronía (SyncNet/LSE) y comparación A/B de resultados |
| 3 | OAuth con Google Drive/Dropbox/OneDrive; búsqueda en Vimeo; más repositorios abiertos (p. ej., NASA, Europeana) |
| 4 | PostgreSQL + Redis, almacenamiento S3 con URL prefirmadas, subidas reanudables (tus) |
| 4 | Cuotas y presupuestos por usuario, 2FA, limitación de intentos, CSP con nonces |
| 4 | Detección de desnudos (NSFW) y de rostros de menores en el vídeo como control reforzado |
| 4 | Firma electrónica del consentimiento y verificación de identidad opcional |
| 5 | Marca de agua invisible robusta (además de la visible) y verificación pública por hash |
