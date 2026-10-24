# Seguridad

## 1. Uso indebido de la imagen de personas

| Riesgo | Medida | Prueba |
|---|---|---|
| Usar la cara de alguien sin permiso | Sólo personas con consentimiento vigente (autodeclaración o enlace firmado) y alcance «imagen» pueden ser personaje real; se comprueba al crear el personaje, en cada generación y al exportar | `test_third_party_consent_link_flow`, `test_identity_scope_enforced` |
| Consentimiento retirado | Borrado en cascada: fotos, retratos, fotogramas y clips con esa persona, vistas previas y exportaciones | `test_full_generation_pipeline` |
| Menores | Bloqueados por defecto (`ALLOW_MINORS=false`) | `test_minor_identities_blocked` |
| Famosos, deepfakes verosímiles, contenido sexual, fraude, acoso | Compromisos obligatorios (incluido «no_famosos») y filtro de instrucciones y diálogos | `test_instructions_filter`, `test_dialogue_filter_blocks` |
| Quitar el aviso de IA | Marca grabada en el servidor en todas las salidas; las versiones sin marca nunca se sirven (403) | `test_full_generation_pipeline` |

## 2. Sin conexiones de salida
La aplicación no se conecta a ningún servicio externo: no hay claves de API, ni descargas desde Internet, ni
proveedores remotos. Así desaparece el riesgo de SSRF y de fuga de fotos a terceros. Una prueba automática
(`test_app_is_free_no_paid_integrations`) falla si alguien añade clientes HTTP o integraciones de pago al código.

## 3. Resto de la aplicación
Contraseñas PBKDF2, sesiones con hash, archivos sólo con URL firmadas HMAC de 1 h, aislamiento por usuario en
todas las consultas (`test_other_users_cannot_access_project`), FFmpeg sin protocolos de red y con límites de CPU y
tamaño, cabeceras CSP (sólo el propio origen) y HSTS, registros saneados.

### Aislamiento recomendado en producción
Trabajador como usuario sin privilegios, cortafuegos que bloquee las conexiones de salida del contenedor,
sistema de archivos de sólo lectura salvo `/data`, `no-new-privileges` y límites de CPU/memoria.
