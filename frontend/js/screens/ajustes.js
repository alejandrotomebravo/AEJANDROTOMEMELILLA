// Pantalla 10: estado de los módulos, límites, privacidad y gestión de datos.
import { api, auth, capabilities } from '../api.js';
import { h, notice, confirmDialog, toast, fmtDuration, fmtMoney } from '../ui.js';

export async function render(root) {
  const [caps, usage, me] = await Promise.all([capabilities(true), api('/api/system/usage'), api('/api/auth/me')]);
  const mod = (name, st, extra) => h('div', { class: 'list-item' }, h('div', { class: 'grow' }, h('strong', {}, name),
    h('div', {}, h('small', { class: 'muted' }, st.available ? (extra || 'Disponible') : (st.reason || 'No disponible')))),
  h('span', { class: `pill ${st.available ? 'ok' : 'warn'}` }, st.available ? 'Activo' : 'Inactivo'));
  root.append(h('h1', {}, 'Configuración y privacidad'), h('div', { class: 'cols even' },
    h('div', { class: 'card' }, h('h2', {}, 'Módulos del servidor'), h('div', { class: 'list' },
      mod('Detección facial', caps.face_detection, `OpenCV ${caps.face_detection.detector === 'yunet' ? 'YuNet' : 'Haar'} · local, gratuito`),
      mod('Sustitución facial', caps.face_swap, `${caps.face_swap.provider} · local (${caps.face_swap.device}) · gratuito`),
      mod('Sincronización labial', caps.lipsync, caps.lipsync.paid ? `sync.so · remoto, de pago (≈ ${caps.lipsync.cost_per_second} USD/s)` : 'Modelo local · gratuito'),
      mod('Voces sintéticas', { available: caps.tts.voices > 0, reason: 'No hay voces configuradas.' }, `${caps.tts.voices} voces · ${caps.tts.providers.join(', ')}`),
      mod('Subtítulos automáticos (ASR)', caps.asr, 'faster-whisper · local'),
      mod('Firma C2PA', caps.c2pa, caps.c2pa.signed_with_own_certificate ? 'Certificado propio' : 'Certificado de pruebas'),
      mod('Exportación H.265', { available: caps.export.h265, reason: 'FFmpeg sin libx265.' }, 'Disponible'))),
    h('div', {},
      h('div', { class: 'card' }, h('h2', {}, 'Límites'), h('dl', { class: 'kv' },
        h('dt', {}, 'Tamaño máximo'), h('dd', {}, `${caps.limits.max_upload_mb} MB`),
        h('dt', {}, 'Duración máxima'), h('dd', {}, fmtDuration(caps.limits.max_video_seconds)),
        h('dt', {}, 'Diálogo máximo'), h('dd', {}, fmtDuration(caps.limits.max_dialogue_seconds)),
        h('dt', {}, '4K'), h('dd', {}, caps.limits.allow_4k ? 'Permitido' : 'Desactivado'),
        h('dt', {}, 'Menores'), h('dd', {}, caps.limits.allow_minors ? 'Sólo con consentimiento del representante legal' : 'No permitido'))),
      h('div', { class: 'card' }, h('h2', {}, 'Consumo de servicios de pago'),
        usage.items.length ? h('div', { class: 'list' }, usage.items.map(u => h('div', { class: 'list-item' }, h('span', { class: 'grow' }, `${u.provider}: ${Math.round(u.units)} ${u.unit} (${u.n} usos)`), h('span', { class: 'cost' }, fmtMoney(u.cost || 0)))))
          : h('p', { class: 'muted' }, 'Sin consumo de pago.'),
        h('p', {}, h('strong', {}, `Total: ${fmtMoney(usage.total_cost_usd)}`)), h('small', { class: 'muted' }, 'Importes estimados según las tarifas configuradas; la factura real es la del proveedor.')))),
  h('div', { class: 'card' }, h('h2', {}, 'Privacidad y datos'),
    h('ul', {},
      h('li', {}, 'Sus archivos no se usan para entrenar modelos de IA.'),
      h('li', {}, `Los proyectos sin actividad se eliminan automáticamente a los ${caps.retention.project_days} días; los temporales, a las ${caps.retention.temp_hours} horas.`),
      h('li', {}, 'Si usa la sincronización remota, sólo el fragmento necesario se envía al proveedor mediante un enlace temporal.'),
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
