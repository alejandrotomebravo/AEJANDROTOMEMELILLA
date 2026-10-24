// Gestión de identidades autorizadas (personas cuyos rostros o voces pueden usarse).
import { api, upload, assetUrl, capabilities } from '../api.js';
import { h, field, checkbox, modal, toast, notice, confirmDialog, clear, spinner, fmtDate } from '../ui.js';

const STATUS = {
  pending: ['Pendiente de consentimiento', 'warn'], verified: ['Consentimiento verificado', 'ok'],
  verified_self: ['Autodeclarado (yo)', 'ok'], revoked: ['Consentimiento retirado', 'err'],
};
const VIEWS = { frontal: 'frontal', tres_cuartos: '¾', perfil: 'perfil' };

export async function render(root) {
  root.append(h('h1', {}, 'Personas autorizadas'),
    h('p', { class: 'muted' }, 'Sólo se pueden usar rostros y voces de personas que lo hayan autorizado. Para terceros se envía un enlace de consentimiento que la persona acepta desde su propio dispositivo y puede retirar en cualquier momento.'));
  const box = h('div');
  root.append(box);
  await identityManager(box);
}

export async function identityManager(container, onChange = () => {}) {
  const caps = await capabilities();
  const list = h('div', { class: 'list' }, spinner());
  const add = h('button', { class: 'btn primary', onclick: () => createDialog(caps).then(ok => ok && refresh()) }, '+ Añadir persona');
  clear(container).append(h('div', { class: 'row', style: { marginTop: 0, marginBottom: '10px' } }, add), list);
  async function refresh() {
    const ids = await api('/api/identities');
    clear(list);
    if (!ids.length) list.append(notice('info', 'Todavía no hay personas. Empiece por usted mismo («Yo») o añada a alguien que le haya dado permiso.'));
    for (const i of ids) list.append(await identityCard(i, refresh));
    onChange(ids);
  }
  await refresh();
  return refresh;
}

async function identityCard(i, refresh) {
  const [label, cls] = STATUS[i.consent_status] || [i.consent_status, ''];
  const photos = h('div', { class: 'photos' });
  for (const p of i.photos) {
    const im = h('img', { alt: `Foto ${VIEWS[p.view] || ''}` });
    assetUrl(p.id).then(u => { im.src = u; }).catch(() => {});
    photos.append(h('div', { class: 'ph' }, im, h('small', {}, VIEWS[p.view] || '')));
  }
  const file = h('input', { type: 'file', accept: 'image/*', multiple: true, hidden: true, onchange: async () => {
    for (const f of file.files) {
      const fd = new FormData();
      fd.append('file', f);
      try {
        const r = await upload(`/api/identities/${i.id}/photos`, fd);
        toast(`Foto añadida (${VIEWS[r.view] || r.view})${r.warnings.length ? ': ' + r.warnings.join(' ') : ''}`, r.warnings.length ? 'warn' : 'ok');
      } catch (e) { toast(e.message, 'error'); }
    }
    refresh();
  } });
  const revoked = i.consent_status === 'revoked';
  const actions = h('div', { class: 'row' },
    !revoked ? h('button', { class: 'btn small', onclick: () => file.click() }, 'Añadir fotos') : null,
    i.kind === 'self' && i.consent_status === 'pending' ? h('button', { class: 'btn small primary', onclick: () => selfAttest(i).then(ok => ok && refresh()) }, 'Confirmar que soy yo') : null,
    i.kind === 'third_party' && !revoked ? h('button', { class: 'btn small primary', onclick: () => consentLink(i).then(refresh) }, i.consent_status === 'verified' ? 'Nuevo enlace' : 'Enviar enlace de consentimiento') : null,
    !revoked && i.consent_status !== 'pending' ? h('button', { class: 'btn small danger', onclick: async () => {
      if (!await confirmDialog('Retirar consentimiento', 'Se borrarán las fotos, los vídeos procesados con esta persona volverán al original y no se podrá volver a usar.', 'Retirar', 'danger')) return;
      await api(`/api/identities/${i.id}/revoke`, { method: 'POST' }); refresh();
    } }, 'Retirar') : null,
    h('button', { class: 'btn small ghost', onclick: async () => {
      if (!await confirmDialog('Eliminar persona', 'Se borrarán sus fotos y registros de uso en proyectos.', 'Eliminar', 'danger')) return;
      await api(`/api/identities/${i.id}`, { method: 'DELETE' }); refresh();
    } }, 'Eliminar'), file);
  const views = new Set(i.photos.map(p => p.view));
  return h('div', { class: 'card flat', style: { marginBottom: 0 } },
    h('div', { class: 'row', style: { marginTop: 0 } }, h('strong', {}, i.display_name), h('span', { class: `pill ${cls}` }, label),
      h('span', { class: 'pill' }, i.kind === 'self' ? 'Yo' : 'Tercero'), ...i.scopes.map(s => h('span', { class: 'pill' }, s)),
      i.is_minor ? h('span', { class: 'pill warn' }, 'Menor') : null),
    photos,
    !revoked && i.photos.length < 3 ? h('small', { class: 'muted' }, `Recomendado: 3 fotos (frontal, ¾ y perfil). Tiene: ${[...views].map(v => VIEWS[v]).join(', ') || 'ninguna'}.`) : null,
    i.consents?.[0] ? h('div', {}, h('small', { class: 'muted' }, `Último consentimiento: ${i.consents[0].status} · ${fmtDate(i.consents[0].created_at)}`)) : null,
    actions);
}

async function createDialog(caps) {
  const name = h('input', { type: 'text', maxlength: 80, placeholder: 'Nombre para identificarla' });
  let kind = 'self';
  const kindSel = h('div', { class: 'tabs' });
  const drawKind = () => kindSel.replaceChildren(
    h('button', { type: 'button', class: `btn small ${kind === 'self' ? 'active' : ''}`, onclick: () => { kind = 'self'; drawKind(); minor.el.hidden = true; } }, 'Yo mismo'),
    h('button', { type: 'button', class: `btn small ${kind === 'third_party' ? 'active' : ''}`, onclick: () => { kind = 'third_party'; drawKind(); minor.el.hidden = !caps.limits.allow_minors; } }, 'Otra persona'));
  drawKind();
  const scopes = { imagen: true, voz: false };
  const img = checkbox('Imagen (rostro)', true, v => { scopes.imagen = v; });
  const voz = checkbox('Voz', false, v => { scopes.voz = v; });
  let isMinor = false;
  const minor = checkbox('Es menor de edad (requiere consentimiento de su representante legal)', false, v => { isMinor = v; });
  minor.el.hidden = true;
  const ok = await modal({ title: 'Añadir persona', body: h('div', {}, kindSel, field('Nombre', name), h('div', { class: 'field' }, h('span', { class: 'label' }, 'Qué se autoriza'), img.el, voz.el), minor.el,
    !caps.limits.allow_minors ? notice('info', 'Por protección de menores, este servidor no permite usar la imagen ni la voz de menores de edad.') : null),
  actions: [{ label: 'Cancelar', value: false }, { label: 'Crear', value: true, kind: 'primary', validate: () => !!name.value.trim() || (toast('Escriba un nombre', 'warn'), false) }] });
  if (!ok) return false;
  try {
    await api('/api/identities', { method: 'POST', body: { display_name: name.value, kind, is_minor: isMinor, scopes: Object.keys(scopes).filter(k => scopes[k]) } });
    return true;
  } catch (e) { toast(e.message, 'error'); return false; }
}

async function selfAttest(i) {
  const name = h('input', { type: 'text', placeholder: 'Nombre y apellidos' });
  const st = { adult: false, me: false };
  const a = checkbox('Soy mayor de edad.', false, v => { st.adult = v; });
  const b = checkbox('Las fotos que subo son mías y autorizo el uso de mi imagen/voz en mis proyectos.', false, v => { st.me = v; });
  const ok = await modal({ title: 'Confirmar identidad propia', body: h('div', {}, field('Nombre completo', name), a.el, b.el,
    notice('info', 'Se registra su declaración con fecha. Es una verificación por declaración responsable, no una comprobación biométrica.')),
  actions: [{ label: 'Cancelar', value: false }, { label: 'Confirmar', value: true, kind: 'primary' }] });
  if (!ok) return false;
  try {
    await api(`/api/identities/${i.id}/self-attest`, { method: 'POST', body: { full_name: name.value, confirm_adult: st.adult, confirm_is_me: st.me } });
    toast('Identidad confirmada', 'ok');
    return true;
  } catch (e) { toast(e.message, 'error'); return false; }
}

async function consentLink(i) {
  const purpose = h('input', { type: 'text', maxlength: 300, placeholder: 'Ej.: vídeo de humor para la cena de empresa' });
  const scopes = { imagen: i.scopes.includes('imagen'), voz: i.scopes.includes('voz') };
  const a = checkbox('Imagen (rostro)', scopes.imagen, v => { scopes.imagen = v; });
  const b = checkbox('Voz', scopes.voz, v => { scopes.voz = v; });
  const ok = await modal({ title: `Solicitar consentimiento a ${i.display_name}`, body: h('div', {}, field('Finalidad concreta', purpose), a.el, b.el),
    actions: [{ label: 'Cancelar', value: false }, { label: 'Generar enlace', value: true, kind: 'primary' }] });
  if (!ok) return;
  try {
    const r = await api(`/api/identities/${i.id}/consent-request`, { method: 'POST', body: { purpose: purpose.value, scopes: Object.keys(scopes).filter(k => scopes[k]), guardian: i.is_minor } });
    const link = r.link.startsWith('http') ? r.link : `${location.origin}/${r.link.replace(/^\//, '')}`;
    const input = h('input', { type: 'text', readonly: true, value: link });
    await modal({ title: 'Enlace de consentimiento', wide: true, body: h('div', {},
      h('p', {}, `Envíe este enlace personal a ${i.display_name}. Caduca en ${r.expires_in_days} días y sólo puede usarse una vez.`), input,
      h('div', { class: 'row' },
        h('button', { class: 'btn small', onclick: () => { navigator.clipboard?.writeText(link); toast('Copiado', 'ok'); } }, 'Copiar'),
        navigator.share ? h('button', { class: 'btn small', onclick: () => navigator.share({ title: 'Solicitud de consentimiento', text: 'Te pido permiso para usar tu imagen en un vídeo con IA:', url: link }).catch(() => {}) }, 'Compartir…') : null),
      h('h3', { style: { marginTop: '14px' } }, 'Texto que verá la persona'), h('div', { class: 'statement' }, r.statement)),
    actions: [{ label: 'Cerrar', value: true, kind: 'primary' }] });
  } catch (e) { toast(e.message, 'error'); }
}
