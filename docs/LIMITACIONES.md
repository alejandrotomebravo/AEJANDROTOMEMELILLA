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

**Uso previsto: propio y particular (no comercial).** Eso permite usar también modelos con licencia «sólo uso no
comercial», que suelen ser los de mejor calidad:

| Paso | Candidato abierto | Licencia | ¿Válido para uso particular? |
|---|---|---|---|
| Guion creativo | Modelo de lenguaje local con Ollama (p. ej. familia Qwen) | Ollama MIT; Qwen, en general Apache 2.0 (revisar cada modelo) | Sí |
| Retratos con la cara | SDXL + InstantID / PuLID / IP‑Adapter FaceID | SDXL: OpenRAIL++; los métodos de identidad usan modelos de InsightFace **de uso no comercial** | Sí (no comercial) |
| Retratos de mayor calidad | FLUX.1 [dev] + PuLID‑FLUX | FLUX.1 [dev]: licencia **no comercial** de Black Forest Labs | Sí (no comercial) |
| Fotogramas | SDXL o FLUX con los retratos como referencia | Igual que arriba | Sí (no comercial) |
| Clips | Wan 2.2 imagen→vídeo vía ComfyUI | Apache 2.0 | Sí |

⚠ Si algún día la app se usara con fines comerciales (vender vídeos, encargos, publicidad), habría que sustituir
los componentes marcados «no comercial» o adquirir sus licencias.

Los módulos `gen/llm.py`, `gen/images.py` y `gen/video.py` ya están separados para enchufar estos modelos.

### Otras mejoras
| Prioridad | Mejora |
|---|---|
| Media | Música libre de derechos incluida; transiciones cruzadas entre escenas |
| Media | Voces Piper en español preinstaladas en la imagen Docker |
| Baja | PostgreSQL + almacenamiento S3, subidas reanudables, 2FA, firma electrónica del consentimiento |
