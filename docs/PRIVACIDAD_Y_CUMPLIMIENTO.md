# Privacidad, derechos y cumplimiento normativo

> Resumen técnico de las medidas implementadas y del marco aplicable en España/UE a 24/10/2026.
> **No es asesoramiento jurídico.** Antes de ofrecer el servicio a terceros, revíselo con un profesional.

## 0. Uso propio y particular (caso de esta instalación)

La aplicación se usa **para fines propios y particulares, sin actividad profesional ni comercial**. Efectos:

| Norma | ¿Se aplica en uso particular? | Matiz importante |
|---|---|---|
| Reglamento de IA (UE) 2024/1689 — obligaciones del responsable del despliegue, incl. art. 50.4 (avisar de las ultrafalsificaciones) | **No**: el art. 2.10 excluye a las personas físicas que usan sistemas de IA en una actividad puramente personal y no profesional | La marca de agua se mantiene igualmente: es una buena práctica y protege si un vídeo se reenvía |
| RGPD / LOPDGDD | **No**, por la «excepción doméstica» (art. 2.2.c RGPD) | Deja de aplicarse si el vídeo se publica en Internet de forma accesible a un número indeterminado de personas (TJUE, asunto C‑101/01, *Lindqvist*) |
| LO 1/1982 (derecho a la propia imagen) | **Sí, siempre** | Usar o difundir la cara o la voz de otra persona sin su consentimiento puede ser una intromisión ilegítima aunque el uso sea particular: el sistema de consentimiento de la app sigue siendo necesario para terceros |
| Código Penal (p. ej. art. 197, intimidad; 173, trato degradante; 401, usurpación de estado civil) | **Sí, siempre** | Los usos prohibidos de la app siguen prohibidos |
| Licencias de los modelos | Permite modelos «sólo uso no comercial» | Ver LIMITACIONES, hoja de ruta |

En resumen: para **sus propios vídeos con su cara** el uso particular es muy sencillo; con la **cara de otras
personas** sigue necesitando su consentimiento, y **publicar** los vídeos en abierto activa de nuevo el RGPD.
No es asesoramiento jurídico.

## 1. Marco normativo relevante (si se ofreciera a terceros)

| Norma | Relevancia | Medida en la app |
|---|---|---|
| **Reglamento (UE) 2024/1689 de IA, art. 50.4** (ultrafalsificaciones) | Quien despliega un sistema que genera o manipula imagen/audio/vídeo que constituya una ultrafalsificación debe indicar que el contenido ha sido generado o manipulado artificialmente. Aplicable desde el **2/8/2026**; en obras manifiestamente artísticas, satíricas o de ficción la obligación se limita a revelarlo de forma que no dificulte la exhibición. | Aviso visible «VÍDEO GENERADO CON INTELIGENCIA ARTIFICIAL» en todas las salidas (también retratos y fotogramas), configurable pero no eliminable; etiqueta obligatoria «PERSONAJES BASADOS EN PERSONAS REALES» cuando procede; etiquetas PARODIA/FICCIÓN/CARICATURA. |
| **Art. 50.2** (marcado legible por máquina) | Según el acuerdo provisional del «Ómnibus digital» (mayo 2026), aplazado al **2/12/2026** para sistemas ya comercializados. ⚠ **Cambio reciente: confirme el texto final publicado en el DOUE.** | Metadatos MP4 + manifiesto C2PA `trainedAlgorithmicMedia` (si `c2patool` está instalado) + JSON de procedencia. |
| **RGPD y LOPDGDD** | Imagen y voz de personas identificables son datos personales. La app no calcula plantillas biométricas: las fotos se envían al modelo de imagen como referencia visual. Menores: en España, consentimiento propio a partir de 14 años (art. 7 LOPDGDD). | Consentimiento registrado y revocable, minimización (IP/agente seudonimizados), supresión de cuenta y proyectos, exportación de datos, conservación limitada, sin entrenamiento de modelos. |
| **LO 1/1982** (honor, intimidad y propia imagen) | Uso de la imagen/voz de una persona requiere su consentimiento expreso; para menores, reglas reforzadas (art. 3) y protección de la LO 1/1996. | Enlace de consentimiento con texto versionado; menores bloqueados por defecto. |
| **Ley de Propiedad Intelectual** | Modificar y difundir obras ajenas requiere autorización salvo excepciones (p. ej., parodia, art. 39 LPI, con requisitos). | La v0.3 no importa vídeos de terceros: genera el contenido. Las instrucciones no deben pedir copiar personajes, marcas u obras protegidas; el usuario es responsable de la música que añada. |
| **Código Penal** (estafa, usurpación de identidad, delitos contra la intimidad, art. 197) | Usos prohibidos. | Compromisos obligatorios, filtro de instrucciones y diálogos, rechazo del modelo, auditoría. |

⚠ En España se ha tramitado legislación específica sobre el uso de la imagen y la voz mediante IA. Su estado
puede haber cambiado: verifíquelo antes del lanzamiento.

## 2. Medidas técnicas y organizativas implementadas

- **Consentimiento verificable**: enlace único (hash del token almacenado), caducidad, texto exacto versionado con SHA‑256, nombre, fecha, IP y agente seudonimizados (HMAC), revocación por el propio interesado.
- **Efectos de la revocación**: se borran sus fotos, sus retratos, los fotogramas y clips en los que aparece, y las vistas previas y exportaciones de los proyectos afectados; se bloquea cualquier nueva generación o exportación.
- **Menores**: bloqueados (`ALLOW_MINORS=false`). Si se habilita: sólo con consentimiento del representante legal por enlace.
- **Marca de agua** aplicada en el servidor; nunca se sirven retratos, fotogramas ni clips sin marca.
- **Aviso al exportar**: modal que explica que la marca no sustituye las autorizaciones de derechos de autor, imagen o voz; su aceptación se registra.
- **Cifrado en tránsito** (HTTPS vía Caddy, HSTS); **en reposo** mediante volumen cifrado.
- **Acceso a archivos** sólo mediante URL firmadas HMAC de 1 h; aislamiento por usuario en todas las consultas.
- **Sin proveedores remotos**: todo (fotos, textos, voces, vídeos) se procesa en el propio servidor; nada se envía a terceros.
- **Conservación**: temporales 24 h, proyectos inactivos 30 días (configurable).
- **Sin entrenamiento**: la aplicación no contiene ninguna ruta de entrenamiento; los archivos no se comparten salvo con el proveedor de procesamiento elegido.
- **Registro técnico** sin datos personales (rutas y correos se eliminan de los mensajes de error).

## 3. Pendiente para un lanzamiento público
Política de privacidad y condiciones de uso, registro de actividades de tratamiento, evaluación de impacto
(EIPD) por el tratamiento de imagen/voz a escala, contratos de encargado con el proveedor de alojamiento si el servidor no es propio, canal de denuncias de contenido.
