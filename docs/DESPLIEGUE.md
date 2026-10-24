# Procedimiento de despliegue

## Opción A · Un servidor con Docker Compose (recomendada para empezar)

1. Servidor Linux con Docker y un dominio apuntando a su IP.
2. **Volumen cifrado** para los datos (LUKS en disco propio, o volumen cifrado del proveedor: EBS, Persistent Disk, etc.). La aplicación no cifra los archivos a nivel de aplicación porque FFmpeg necesita acceso aleatorio; el cifrado en reposo lo da el volumen.
3. Configuración:
   ```bash
   cp .env.example .env
   # APP_ENV=production, SECRET_KEY=<aleatorio>, PUBLIC_BASE_URL=https://su-dominio, DATA_DIR=/data
   # opcional: ANTHROPIC_API_KEY y FAL_KEY para generar con IA
   mkdir -p models   # pesos opcionales (YuNet, voces Piper…) — no se versionan
   cd deploy && DOMAIN=su-dominio docker compose up -d --build
   ```
4. Caddy obtiene el certificado TLS y redirige HTTP→HTTPS (cifrado en tránsito). HSTS activado.
5. Compruebe `https://su-dominio/api/system/health` y **Ajustes → Módulos**.

## Opción B · API + trabajador separado

- API (CPU, 2 vCPU/4 GB) y trabajador (CPU; FFmpeg para render) comparten la **misma base de datos y el mismo volumen** (NFS/EFS o disco compartido). Con `INLINE_WORKER=false` en ambos y `python -m app.worker` en el trabajador.
- SQLite sobre NFS no es fiable con escrituras concurrentes intensas: para varios nodos, migre a PostgreSQL (está previsto en la Fase 4; el acceso a datos está aislado en `db.py` y `jobs/queue.py`).
- La generación con IA se hace en fal.ai y Claude: **no hace falta GPU**. Una GPU sólo tendría sentido para un proveedor de vídeo autoalojado (no incluido).

## Lista de comprobación de producción

- [ ] `APP_ENV=production` y `SECRET_KEY` aleatoria (la app no arranca sin ella).
- [ ] HTTPS obligatorio (Caddy) y `PUBLIC_BASE_URL` correcto (enlaces de consentimiento).
- [ ] Volumen de datos cifrado y copias de seguridad cifradas.
- [ ] Condiciones de uso de fal.ai y Anthropic revisadas (consentimiento escrito para la imagen de personas reales) y tarifas `FAL_*` actualizadas.
- [ ] `ALLOW_MINORS=false` salvo análisis jurídico.
- [ ] Certificado C2PA propio si se quiere procedencia verificable.
- [ ] Política de privacidad, aviso legal y contratos de encargado del tratamiento con fal.ai, Anthropic y ElevenLabs si se usan.
- [ ] Límites (`MAX_UPLOAD_MB`, `MAX_SCENES`) acordes con el disco y la CPU/GPU disponibles.
- [ ] Monitorización de `/api/system/health`, espacio en disco y trabajos en estado `failed`.

## Escalado

| Carga | Configuración |
|---|---|
| Uso personal / pruebas | 1 servidor CPU, INLINE_WORKER, Claude + fal.ai |
| Decenas de usuarios | API + 1‑2 trabajadores, `WORKER_CONCURRENCY` 2‑4 (las esperas son de red) |
| Cientos de usuarios | PostgreSQL, almacenamiento de objetos (S3) con URL prefirmadas, varios trabajadores, cuotas por usuario (pendiente, Fase 4) |
