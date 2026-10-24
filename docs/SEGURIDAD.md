# Seguridad

## 1. Uso indebido de la imagen de personas

| Riesgo | Medida | Prueba |
|---|---|---|
| Usar la cara de alguien sin permiso | Sólo personas con consentimiento vigente (autodeclaración o enlace firmado) y alcance «imagen» pueden ser personaje real; se comprueba al crear el personaje, en cada generación y al exportar | `test_third_party_consent_link_flow`, `test_identity_scope_enforced` |
| Consentimiento retirado | Borrado en cascada: fotos, retratos, fotogramas y clips con esa persona, vistas previas y exportaciones | `test_full_generation_pipeline` |
| Menores | Bloqueados por defecto (`ALLOW_MINORS=false`) | `test_minor_identities_blocked` |
| Famosos, deepfakes verosímiles, contenido sexual, fraude, acoso | Compromisos obligatorios (incluido «no_famosos»), filtro de instrucciones y diálogos, rechazo del propio modelo (Claude) y filtros del proveedor de imagen/vídeo | `test_instructions_filter`, `test_claude_refusals`, `test_dialogue_filter_blocks` |
| Quitar el aviso de IA | Marca grabada en el servidor en todas las salidas; las versiones sin marca nunca se sirven (403) | `test_full_generation_pipeline` |

## 2. Red de salida (descarga de resultados de fal.ai)

| Amenaza | Medida (`app/net.py`) | Prueba (`tests/test_net.py`) |
|---|---|---|
| Protocolos peligrosos, credenciales en la URL, puertos raros | Sólo `http`/`https`, puertos 80/443 | `test_blocked_urls` |
| **SSRF** a la red interna o metadatos de nube | Todas las IP resueltas deben ser públicas; nombres internos bloqueados; IPv4 en IPv6 y formatos numéricos cubiertos | `test_blocked_urls`, `test_hostname_resolving_to_private_ip_is_blocked` |
| *DNS rebinding* | Conexión fijada a la IP validada con TLS verificado contra el nombre | — |
| Redirecciones a la red interna | Cada salto se valida; máximo `MAX_REDIRECTS` | `test_redirect_to_internal_address_is_blocked`, `test_fal_download_blocks_internal_redirect_and_non_media` |
| Ejecutables o páginas web en lugar de imágenes/vídeo | Firma binaria + validación con OpenCV/ffprobe del resultado | `test_download_rejects_non_video`, `test_fal_bad_image_rejected` |
| Archivos enormes | Límites antes y durante la descarga | `test_download_size_limits` |

`ALLOW_PRIVATE_NETWORKS=true` sólo para desarrollo: la app no arranca con esa opción en producción.

## 3. Claves y costes
- `ANTHROPIC_API_KEY` y `FAL_KEY` sólo existen en el servidor; nunca llegan al navegador.
- Cada generación de pago exige confirmación del coste estimado (HTTP 402 `confirmar_coste` sin `confirm_cost`).
- Las peticiones a fal.ai se reanudan por su identificador tras un reinicio: no se paga dos veces el mismo trabajo.
- El consumo se registra por usuario (Ajustes → Consumo).

## 4. Resto de la aplicación
Contraseñas PBKDF2, sesiones con hash, archivos sólo con URL firmadas HMAC de 1 h, aislamiento por usuario en
todas las consultas (`test_other_users_cannot_access_project`), FFmpeg sin protocolos de red y con límites de CPU y
tamaño, cabeceras CSP (sólo el propio origen) y HSTS, registros saneados.

### Aislamiento recomendado en producción
Trabajador como usuario sin privilegios, proxy de salida (`OUTBOUND_PROXY`) que bloquee rangos privados,
sistema de archivos de sólo lectura salvo `/data`, `no-new-privileges` y límites de CPU/memoria.
