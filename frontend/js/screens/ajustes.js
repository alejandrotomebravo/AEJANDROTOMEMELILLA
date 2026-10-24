// Pantalla 10: estado de los módulos, límites, privacidad y gestión de datos.
import { api, auth, capabilities } from '../api.js';
import { h, notice, confirmDialog, toast, fmtDuration } from '../ui.js';

export async function render(root) {
  const [caps, me] = await Promise.all([capabilities(true), api('/api/auth/me')]);
  const mod = (name, st, extra) => h('div', { class: 'list-item' }, h('div', { class: 'grow' }, h('strong', {}, name),
    h('div', {}, h('small', { class: 'muted' }, st.available ? (extra || 'Disponible') : (st.reason || 'No disponible')))),
  h('span', { class: `pill ${st.available ? 'ok' : 'warn'}` }, st.available ? 'Activo' : 'Inactivo'));
  root.append(h('h1', {}, 'Configuración y privacidad'), h('div', { class: 'cols even' },
    h('div', { class: 'card' }, h('h2', {}, 'Módulos del servidor'), h('div', { class: 'list' },
      mod('Guion y prompts', caps.llm, 'Por reglas · local, gratuito'),
      mod('Imágenes (retratos y fotogramas)', caps.image, 'Filtros de estilo y composición · local, gratuito'),
      mod('Vídeo (animación de escenas)', caps.video, 'Animática con movimiento de cámara · local, gratuito'),
      mod('Detección facial (validar fotos)', caps.face_detection, `OpenCV ${caps.face_detection.detector === 'yunet' ? 'YuNet' : 'Haar'} · local, gratuito`),
      mod('Voces sintéticas', { available: caps.tts.voices > 0, reason: 'No hay voces configuradas.' }, `${caps.tts.voices} voces · ${caps.tts.providers.join(', ')}`),
      mod('Subtítulos automáticos (ASR)', caps.asr, 'faster-whisper · local'),
      mod('Firma C2PA', caps.c2pa, caps.c2pa.signed_with_own_certificate ? 'Certificado propio' : 'Certificado de pruebas'),
      mod('Exportación H.265', { available: caps.export.h265, reason: 'FFmpeg sin libx265.' }, 'Disponible'))),
    h('div', {},
      h('div', { class: 'card' }, h('h2', {}, 'Límites'), h('dl', { class: 'kv' },
        h('dt', {}, 'Tamaño máximo de subida'), h('dd', {}, `${caps.limits.max_upload_mb} MB`),
        h('dt', {}, 'Diálogo máximo'), h('dd', {}, fmtDuration(caps.limits.max_dialogue_seconds)),
        h('dt', {}, '4K'), h('dd', {}, caps.limits.allow_4k ? 'Permitido' : 'Desactivado'),
        h('dt', {}, 'Menores'), h('dd', {}, caps.limits.allow_minors ? 'Sólo con consentimiento del representante legal' : 'No permitido'))))),
  h('div', { class: 'card' }, h('h2', {}, 'Privacidad y datos'),
    h('ul', {},
      h('li', {}, 'Sus archivos no se usan para entrenar modelos de IA.'),
      h('li', {}, `Los proyectos sin actividad se eliminan automáticamente a los ${caps.retention.project_days} días; los temporales, a las ${caps.retention.temp_hours} horas.`),
      h('li', {}, 'Todo se procesa en este servidor: sus fotos, textos y vídeos no se envían a ningún servicio externo.'),
      h('li', {}, 'La aplicación es gratuita: no usa servicios de pago ni claves de API.'),
      h('li', {}, 'Los registros técnicos no guardan rutas, correos ni contenido de los archivos.')),
    h('p', { class: 'muted' }, `Sesión iniciada como ${me.email}.`),
    h('div', { class: 'row' },
      h('button', { class: 'btn', onclick: async () => {
        const data = await api('/api/system/privacy/export');
        const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
        const a = h('a', { href: URL.createObjectURL(blob), download: 'mis-datos.json' });
        document.body.append(a); a.click(); a.remove();
      } }, 'Descargar mis datos'),
      h('button', { class: 'btn', onclick: async () => { await api('/api/auth/logout', { method: 'POST' }); auth.token = null; location.hash = '#/entrar'; } }, 'Cerrar sesión'),
      h('button', { class: 'btn danger', onclick: async () => {
        if (!await confirmDialog('Eliminar cuenta', 'Se borrarán de forma permanente su cuenta, proyectos, personas, fotos, audios y exportaciones.', 'Eliminar todo', 'danger')) return;
        await api('/api/auth/account', { method: 'DELETE' });
        auth.token = null; toast('Cuenta eliminada', 'ok'); location.hash = '#/entrar';
      } }, 'Eliminar mi cuenta y todos mis datos'))),
  notice('info', `Versión ${caps.version}. La marca de agua no sustituye a las autorizaciones de derechos de autor, imagen o voz.`));
}
