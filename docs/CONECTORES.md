# Fuentes de vídeo de Internet: estado real y cómo añadir nuevas

> Verificación de condiciones y API a **24/10/2026**. Las condiciones de las plataformas cambian: revíselas antes de
> habilitar un conector en producción. El entorno donde se desarrolló **no tenía acceso a estas plataformas**, así que
> los conectores se han probado con respuestas que siguen la estructura de la documentación oficial y con descargas
> reales desde un servidor local. **Antes de producción, pruébelos contra el servicio real** (ver §4).

## 1. Qué se puede importar realmente

| Fuente | Método | Búsqueda | Importación | Requisitos | Condiciones y límites |
|---|---|---|---|---|---|
| **Wikimedia Commons** | B · API MediaWiki | ✅ | ✅ | Ninguno (User-Agent identificable, `HTTP_USER_AGENT`) | Licencias libres o dominio público; la API da licencia y autor (`extmetadata`). Se bloquea CC‑ND. Pueden existir derechos de la personalidad de quien aparece (campo *Restrictions*). |
| **Internet Archive** | B · API de búsqueda y metadatos | ✅ (opción «sólo licencias abiertas») | ✅ | Ninguno | La licencia (`licenseurl`) no siempre consta: si falta, el usuario debe acreditar derechos. Elementos de préstamo restringido: no se importan. |
| **Pexels** | B · API oficial | ✅ | ✅ | `PEXELS_API_KEY` (gratuita) | Licencia de Pexels: uso y modificación gratuitos; prohíbe mostrar a personas identificables de forma ofensiva o sugerir respaldo. **No equivale al consentimiento de las personas filmadas** para alterar su rostro o voz. |
| **Vimeo** | B · API oficial | ❌ (sólo URL) | ⚠️ Sólo si la API devuelve el campo `download` | `VIMEO_ACCESS_TOKEN` con permiso `video_files`; plan del propietario que lo permita | En la práctica, vídeos propios. No se usan contraseñas. Sin enlace de descarga → no importable. |
| **YouTube** | Sólo metadatos (oEmbed / Data API v3) | ✅ con `YOUTUBE_API_KEY` | ❌ **Nunca** | — | Sus condiciones prohíben descargar salvo con funciones del propio servicio; la API no ofrece descarga. Se muestran alternativas: descarga de vídeos propios desde YouTube Studio, pedir el archivo al autor o buscar material con licencia abierta. |
| **Enlace directo** | A · descarga segura | — | ✅ | — | Sólo archivos de vídeo (no páginas web ni HLS/DASH). Licencia desconocida: el usuario declara sus derechos. |
| **Nube del usuario** (iCloud Drive, Google Drive, Dropbox…) | C · selector de archivos del sistema | — | ✅ | — | Se usa el selector de Archivos del iPhone/ordenador. Integración directa por OAuth: pendiente (requiere registrar la app en cada proveedor). |

**No implementado deliberadamente:** extractores de páginas web (*scraping*) o de plataformas que no permiten la descarga
(como yt-dlp para YouTube), elusión de DRM, inicios de sesión con usuario/contraseña. Son frágiles y, en muchos casos,
contrarios a las condiciones de servicio.

## 2. Permisos distintos que la aplicación separa

1. **Acceder o descargar** — lo concede la fuente (API, licencia, enlace directo).
2. **Modificar** — lo declara el usuario (`can_modify`, obligatorio); se bloquea si la licencia lo prohíbe (CC‑ND) salvo autorización expresa.
3. **Difundir el resultado** — `can_distribute`; si falta, se avisa al exportar.
4. **Imagen y voz de quienes aparecen** — consentimiento de esas personas (`third_party_people_consent`), independiente de la licencia del vídeo.

Se guarda: URL original, plataforma, autor, licencia, fecha de importación, SHA‑256 del archivo, fragmento y la declaración
del usuario. Si la licencia exige atribución, los créditos se muestran al final del vídeo (desactivable con aviso), se
incluyen en los metadatos MP4 (`copyright`) y en el informe `procedencia.json`.

## 3. Cómo añadir una fuente nueva

1. Cree una clase en `backend/app/sources/connectors.py` (o en un módulo propio) que herede de `Connector`:

   ```python
   class MiFuente(Connector):
       name = "mi_fuente"            # identificador interno
       label = "Mi fuente"           # nombre visible
       method = "B"                  # A: URL directa · B: API oficial
       searchable = True
       requires_key = True
       terms_url = "https://…/terminos"
       docs_url = "https://…/api"
       description = "Qué ofrece y con qué condiciones."

       def status(self):             # ¿configurada?
           return {"available": bool(settings.mi_fuente_key), "reason": "Falta MI_FUENTE_KEY"}

       def can_handle(self, url):     # ¿es una URL de esta plataforma?
           return (urlsplit(url).hostname or "").endswith("mifuente.com")

       def search(self, query, page=1, open_only=True):
           data = net.get_json(f"https://api.mifuente.com/search?{urlencode({'q': query})}", headers=…)
           return [self._candidate(x) for x in data["items"]]

       def resolve(self, url):
           …
           return VideoCandidate(connector=self.name, id=…, title=…, page_url=…, platform=self.label,
                                 file_url=<enlace OFICIAL al archivo>, importable=True,
                                 license=cc_license(nombre, url_licencia), …)
   ```

2. **Use siempre `app.net`** (`get_json`, `request`, `download`): aplica la protección anti‑SSRF, los límites y la
   validación de tipo de archivo. No use `requests`/`urllib` directamente.
3. Rellene `LicenseInfo` con condiciones concretas (`cc_license()` interpreta CC y dominio público). Si la licencia no
   permite modificar, devuelva `importable=False` con `reason` y `alternatives`.
4. Si la descarga necesita cabeceras (token), devuélvalas en `download_headers()`; el enlace del archivo nunca se envía
   al navegador.
5. Regístrela en `sources/registry.py` (`ALL`) y añádala a `ENABLED_CONNECTORS`.
6. Añada pruebas en `backend/tests/test_internet.py` con una respuesta de ejemplo de la documentación oficial.
7. Documente aquí sus condiciones de uso.

El editor, la cola de trabajos y la exportación no necesitan cambios.

## 4. Verificación contra los servicios reales (antes de producción)

```bash
cd backend
python - <<'PY'
from app.sources import registry
for url in ["https://commons.wikimedia.org/wiki/File:<un_video>.webm",
            "https://archive.org/details/<identificador>",
            "https://www.youtube.com/watch?v=<id>"]:
    c = registry.resolve(url); print(c.connector, c.importable, c.license.name, c.reason)
PY
```
