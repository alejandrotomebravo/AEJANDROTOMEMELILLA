# Procedimiento de despliegue

## Opción A · Un servidor con Docker Compose (recomendada para empezar)

1. Servidor Linux con Docker y un dominio apuntando a su IP.
2. **Volumen cifrado** para los datos (LUKS en disco propio, o volumen cifrado del proveedor: EBS, Persistent Disk, etc.). La aplicación no cifra los archivos a nivel de aplicación porque FFmpeg necesita acceso aleatorio; el cifrado en reposo lo da el volumen.
3. Configuración:
   ```bash
   cp .env.example .env
   # APP_ENV=production, SECRET_KEY=<aleatorio>, PUBLIC_BASE_URL=https://su-dominio, DATA_DIR=/data
   mkdir -p models   # pesos opcionales (YuNet, voces Piper…) — no se versionan
   cd deploy && DOMAIN=su-dominio docker compose up -d --build
   ```
4. Caddy obtiene el certificado TLS y redirige HTTP→HTTPS (cifrado en tránsito). HSTS activado.
5. Compruebe `https://su-dominio/api/system/health` y **Ajustes → Módulos**.

## Opción B · API + trabajador separado

- API (CPU, 2 vCPU/4 GB) y trabajador (CPU; FFmpeg para render) comparten la **misma base de datos y el mismo volumen** (NFS/EFS o disco compartido). Con `INLINE_WORKER=false` en ambos y `python -m app.worker` en el trabajador.
- SQLite sobre NFS no es fiable con escrituras concurrentes intensas: para varios nodos, migre a PostgreSQL (está previsto en la Fase 4; el acceso a datos está aislado en `db.py` y `jobs/queue.py`).
- No hace falta GPU en esta versión. La fase de IA generativa gratuita sí la necesitará (ver LIMITACIONES).

## Lista de comprobación de producción

- [ ] `APP_ENV=production` y `SECRET_KEY` aleatoria (la app no arranca sin ella).
- [ ] HTTPS obligatorio (Caddy) y `PUBLIC_BASE_URL` correcto (enlaces de consentimiento).
- [ ] Volumen de datos cifrado y copias de seguridad cifradas.
- [ ] `ALLOW_MINORS=false` salvo análisis jurídico.
- [ ] Certificado C2PA propio si se quiere procedencia verificable.
- [ ] Política de privacidad, aviso legal y contrato de encargado con el proveedor de alojamiento si no es un servidor propio.
- [ ] Límites (`MAX_UPLOAD_MB`, `MAX_SCENES`) acordes con el disco y la CPU/GPU disponibles.
- [ ] Monitorización de `/api/system/health`, espacio en disco y trabajos en estado `failed`.

## Escalado

| Carga | Configuración |
|---|---|
| Uso personal / pruebas | 1 servidor CPU (o el ordenador de la oficina), INLINE_WORKER |
| Decenas de usuarios | API + 1‑2 trabajadores, `WORKER_CONCURRENCY` = nº de núcleos / 2 |
| Cientos de usuarios | PostgreSQL, almacenamiento de objetos (S3) con URL prefirmadas, varios trabajadores, cuotas por usuario (pendiente, Fase 4) |
