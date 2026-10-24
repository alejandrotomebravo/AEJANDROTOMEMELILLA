# Limitaciones conocidas y hoja de ruta

## Limitaciones actuales (honestas)

### Generación
- **No hay IA generativa de imagen ni de vídeo** en esta versión gratuita: los retratos son filtros sobre la foto,
  los fotogramas son composiciones (fondo + título + retratos) y los clips son animáticas con movimiento de cámara.
  Sirve para maquetar historias, presentaciones y vídeos sencillos; no para escenas realistas con personajes que
  se mueven. La interfaz lo indica en cada paso.
- El **guion** se crea por reglas (una escena por frase, diálogos entre comillas): no inventa ni mejora la historia.
- **Sin sincronización labial**: las voces se colocan sobre el clip con subtítulos.
- Las **voces** de eSpeak suenan robóticas; Piper (también gratuito) suena mejor. Sin clonación de voz.
- La **reproducción del editor** en el navegador es aproximada; la exacta es la vista previa renderizada.

### Seguridad y cumplimiento
- La **autodeclaración** («soy yo») no es una verificación biométrica ni documental.
- El **consentimiento por enlace** acredita que alguien con acceso al enlace aceptó; no verifica su identidad real.
- El **filtro de instrucciones** es por patrones: tiene falsos positivos y negativos.
- No hay detección automática de **personas famosas** en las fotos subidas: se basa en la declaración y el consentimiento.
- La **marca visible** puede recortarse con un editor externo tras la descarga; los metadatos y C2PA pueden eliminarse
  recodificando. No existe protección técnica absoluta.
- Sin limitación de intentos de inicio de sesión ni 2FA.

### Infraestructura
- SQLite: un único nodo. Archivos en disco local; el cifrado en reposo depende del volumen.
- La base de datos de la v0.2 (intercambio de caras) **no se migra** al esquema v3: empiece con un `DATA_DIR` nuevo.

## Hoja de ruta

### Siguiente fase: IA generativa gratuita con GPU propia
Requiere un ordenador con tarjeta gráfica **NVIDIA** (recomendado ≥ 12 GB de memoria de vídeo; ≥ 24 GB para vídeo
a velocidad razonable). Todo seguiría siendo gratuito por uso y local. Candidatos a evaluar con el equipo real:

| Paso | Candidato abierto | Licencia (a verificar antes de usar) |
|---|---|---|
| Guion creativo | Modelo de lenguaje local servido con Ollama (p. ej. familia Qwen) | Ollama MIT; cada modelo tiene su licencia |
| Retratos con la cara | SDXL + método de identidad facial (InstantID / PuLID / IP‑Adapter FaceID) | SDXL: OpenRAIL++. ⚠ Varios métodos de identidad dependen de modelos de InsightFace **de uso no comercial** |
| Fotogramas | SDXL con los retratos como referencia | Igual que arriba |
| Clips | Wan 2.2 imagen→vídeo vía ComfyUI | Wan 2.2: Apache 2.0 |

Los módulos `gen/llm.py`, `gen/images.py` y `gen/video.py` ya están separados para enchufar estos modelos.

### Otras mejoras
| Prioridad | Mejora |
|---|---|
| Media | Música libre de derechos incluida; transiciones cruzadas entre escenas |
| Media | Voces Piper en español preinstaladas en la imagen Docker |
| Baja | PostgreSQL + almacenamiento S3, subidas reanudables, 2FA, firma electrónica del consentimiento |
