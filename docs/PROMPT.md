# PROYECTO: APLICACIÓN DE IA PARA GENERAR VÍDEOS A PARTIR DE INSTRUCCIONES CON PERSONAJES BASADOS EN ROSTROS AUTORIZADOS

> Prompt de especificación de la versión 0.3. Sustituye al enfoque anterior (sustitución de caras en vídeos
> existentes, conservado en el historial de git, commit `b312abc`).

## 1. Tu papel

Actúa como un equipo sénior de ingeniería de software, IA generativa (texto, imagen y vídeo), dirección
audiovisual, diseño UX/UI, seguridad y cumplimiento normativo.

Desarrolla una aplicación funcional, no una maqueta, que convierta **instrucciones escritas** en un **vídeo
generado por IA** protagonizado por **personajes creados a partir de los rostros de personas que lo hayan
autorizado**, ya sea con apariencia realista o como **caricatura** u otro estilo artístico elegido por el usuario.

Debe funcionar en iPhone y ordenador, estar íntegramente en español y ser modular, para poder cambiar de
proveedor de IA sin reescribir la aplicación.

## 2. Flujo de trabajo (asistente paso a paso)

1. **Proyecto**: nombre, formato (9:16, 16:9, 1:1), estilo visual por defecto y declaración de uso responsable.
2. **Personajes**:
   - A partir de una persona autorizada (fotos + consentimiento verificable de imagen) o un personaje ficticio
     descrito con texto.
   - Elección de **estilo** por personaje: realista, caricatura, cómic, anime, animación 3D, acuarela, boceto a
     lápiz, pixel art, plastilina.
   - Generación de un **retrato de referencia** del personaje en ese estilo, con vista previa y opción de repetir.
3. **Guion e instrucciones**:
   - El usuario describe con sus palabras lo que quiere que ocurra (p. ej. «Ana y Luis celebran en la playa de
     Melilla que han ganado la lotería, en estilo caricatura»).
   - La aplicación **genera el prompt**: un guion gráfico con escenas, plano y movimiento de cámara,
     ambientación, acciones, personajes presentes, duración, diálogos y los prompts optimizados para imagen y
     para vídeo de cada escena.
   - Todo es editable; el usuario puede regenerar el guion completo o una escena.
4. **Escenas**: para cada escena se genera un **fotograma clave** (con los retratos de los personajes como
   referencia) y, después, el **clip de vídeo** a partir de ese fotograma. Vista previa, repetición y tiempo estimado.
5. **Voces y subtítulos**: diálogos por escena con voz sintética autorizada, grabación o archivo de audio;
   subtítulos automáticos a partir del texto.
6. **Editor**: línea de tiempo multipista (escenas generadas, voces, música, subtítulos, textos), orden,
   recorte, velocidad, fundidos, color, volumen, deshacer/rehacer y guardado automático.
7. **Vista previa** renderizada.
8. **Exportación** MP4 (H.264/H.265; 720p/1080p, 4K si procede; 24-60 fps) con marca de agua obligatoria
   «VÍDEO GENERADO CON INTELIGENCIA ARTIFICIAL», etiquetas adicionales (PARODIA, FICCIÓN, CARICATURA,
   PERSONAJES BASADOS EN PERSONAS REALES), metadatos de procedencia (C2PA si es viable), SRT e informe JSON.

## 3. Gratuidad: procesamiento 100 % local

**Requisito del propietario: la aplicación debe ser gratuita.** No se integra ningún servicio de pago ni API
externa (ni de guion, ni de imagen, ni de vídeo, ni de voz), no se piden claves y nada sale del servidor.

| Función | Versión actual (CPU, gratuita) | Fase siguiente con GPU NVIDIA propia (gratuita) |
|---|---|---|
| Guion gráfico y prompts | Por reglas (una escena por frase, diálogos entre «…») | Modelo de lenguaje abierto local (Ollama) |
| Retrato estilizado del personaje | Filtros OpenCV (cómic, anime, boceto, acuarela, pixel art) | SDXL + identidad facial (revisar licencias) |
| Fotograma clave de la escena | Composición: retratos sobre fondo con el título | SDXL con los retratos como referencia |
| Clip de vídeo | «Animática»: movimiento de cámara sobre el fotograma (FFmpeg) | Wan 2.2 imagen→vídeo (Apache 2.0) vía ComfyUI |
| Voz | eSpeak NG / Piper | eSpeak NG / Piper |

No se simulan resultados: la interfaz deja claro que filtros, composiciones y animáticas no son IA generativa.

## 4. Derechos, seguridad y privacidad

- Sólo se pueden usar rostros de personas con **consentimiento verificable de imagen** (autodeclaración para uno
  mismo; enlace de consentimiento revocable para terceros). Una caricatura de una persona real también lo requiere.
- Menores bloqueados por defecto. Personas públicas o famosas: no se pueden crear sin su consentimiento.
- Prohibido: contenido sexual o íntimo, suplantación fraudulenta, acoso, desinformación sobre hechos reales,
  falsificación de pruebas. Filtro de las instrucciones y de los diálogos.
- Revocar el consentimiento elimina fotos, retratos, fotogramas y clips con esa persona y bloquea exportaciones.
- Ningún dato sale del servidor; no entrenar modelos con datos del usuario;
  plazos de conservación; supresión de cuenta y proyectos; registros sin datos personales.
- La marca de agua no sustituye a las autorizaciones de imagen, voz o derechos de autor.

## 5. Arquitectura y calidad

- Backend modular (API + cola de trabajos persistente con reintentos, cancelación y progreso), procesamiento
  multimedia con FFmpeg, almacenamiento seguro, PWA instalable.
- Guion, imagen y vídeo en módulos intercambiables para añadir modelos abiertos con GPU sin tocar la API.
- Pruebas automatizadas, incluida una que impide introducir servicios de pago, y prueba real de extremo a extremo.
- Documentación: arquitectura, instalación, despliegue, costes (cero por uso) y tiempos medidos, licencias y limitaciones.
