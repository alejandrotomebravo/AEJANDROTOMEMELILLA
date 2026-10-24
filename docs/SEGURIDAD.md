# Seguridad

## Extractor de Internet

| Amenaza | Medida (archivo) | Prueba |
|---|---|---|
| URLs maliciosas, protocolos peligrosos (`file:`, `ftp:`, `gopher:`, `javascript:`) | Sólo `http`/`https`, puertos 80/443, sin credenciales en la URL (`net.validate_url`) | `test_blocked_urls` |
| **SSRF** a la red interna o a metadatos de nube (169.254.169.254, `metadata.google.internal`) | Se resuelve el nombre y se exige que **todas** las IP sean públicas; nombres internos (`localhost`, `.local`, `.internal`…) bloqueados; IPv4 en IPv6 (`::ffff:127.0.0.1`) y formatos numéricos (`2130706433`) cubiertos | `test_blocked_urls`, `test_hostname_resolving_to_private_ip_is_blocked` |
| *DNS rebinding* | La conexión se fija a la IP validada (`_PinnedHTTPSConnection`), con TLS verificado contra el nombre original | — |
| Redirecciones hacia la red interna | Cada salto se valida de nuevo; máximo `MAX_REDIRECTS` | `test_redirect_to_internal_address_is_blocked`, `test_too_many_redirects` |
| Ejecutables, comprimidos, HTML, listas HLS disfrazadas | Lista de tipos MIME rechazados + comprobación de la **firma binaria** del archivo (MP4/MOV, Matroska/WebM, Ogg, AVI, MPEG‑TS) | `test_download_rejects_non_video` |
| Archivos enormes, `Content-Length` falso | Límite antes y durante la descarga; archivo parcial eliminado | `test_download_size_limits` |
| Archivos malformados o «bombas» de códec | `ffprobe` con `-protocol_whitelist file` y **lista blanca de contenedores**; FFmpeg sin protocolos de red para entradas no confiables; límites de CPU y tamaño de archivo por subproceso (`MEDIA_CPU_SECONDS`, `MEDIA_MAX_FILE_MB`); normalización a H.264 antes de cualquier otro uso | `test_rejects_non_video_and_disguised_files` |
| Uso indebido de credenciales | No se piden contraseñas de plataformas; las claves de API las configura el administrador; los enlaces de descarga no se exponen al navegador (`PRIVATE_PARAMS`) | `test_import_from_wikimedia_end_to_end` |
| Acceso a proyectos ajenos | Todas las consultas filtran por usuario; archivos sólo con URL firmadas HMAC de 1 h | `test_other_users_cannot_access_project` |
| Desbordamiento de recursos | Límites de tamaño, duración, lote (20 archivos), tiempo de descarga; cola con concurrencia configurable | — |

`ALLOW_PRIVATE_NETWORKS=true` desactiva el bloqueo de IPs privadas **sólo para desarrollo**; la aplicación se niega a
arrancar con esa opción en `APP_ENV=production`.

### Aislamiento recomendado en producción
- Ejecute el trabajador como usuario sin privilegios (la imagen Docker ya lo hace) y, si es posible, en un contenedor separado de la API.
- Salida a Internet a través de un **proxy de salida** (`OUTBOUND_PROXY`) que bloquee también rangos privados: así la protección no depende sólo de la aplicación.
- En Kubernetes/Docker: `read_only` en el sistema de archivos salvo `/data`, `no-new-privileges`, perfil seccomp por defecto y límites de CPU/memoria.

## Resto de la aplicación
Ver [PRIVACIDAD_Y_CUMPLIMIENTO.md](PRIVACIDAD_Y_CUMPLIMIENTO.md): contraseñas PBKDF2, sesiones con hash, URL firmadas,
cabeceras CSP/HSTS, registros saneados, consentimiento revocable, marca de agua aplicada en el servidor.
