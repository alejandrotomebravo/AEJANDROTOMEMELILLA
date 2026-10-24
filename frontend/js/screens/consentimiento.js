// Página pública que abre la persona a la que se pide consentimiento (sin cuenta).
import { api } from '../api.js';
import { h, field, checkbox, toast, notice, clear } from '../ui.js';

export async function render(root, token) {
  let info;
  try { info = await api(`/api/consent/${token}`); } catch (e) {
    root.append(notice('error', e.message)); return;
  }
  const box = h('div', { class: 'card auth-box', style: { maxWidth: '640px' } });
  root.append(box);
  const scopes = info.scopes.map(s => (s === 'imagen' ? 'su imagen (rostro)' : 'su voz')).join(' y ');
  box.append(h('h1', {}, 'Solicitud de consentimiento'),
    h('p', {}, `${info.requester} solicita permiso para usar ${scopes}${info.guardian ? ' del menor ' + info.identity_name : ''} en vídeos editados con inteligencia artificial.`));
  if (info.status === 'granted') {
    box.append(notice('ok', 'Ya dio su consentimiento. Puede retirarlo cuando quiera.'), revokeButton(token, box));
    return;
  }
  if (info.status === 'revoked') { box.append(notice('warn', 'Este consentimiento fue retirado.')); return; }
  if (info.status !== 'pending') { box.append(notice('warn', 'Este enlace ha caducado. Pida uno nuevo si quiere autorizar el uso.')); return; }
  const name = h('input', { type: 'text', autocomplete: 'name', placeholder: 'Nombre y apellidos' });
  const st = { accept: false, adult: false };
  const a = checkbox(info.guardian ? 'Soy mayor de edad y representante legal del menor.' : 'Soy mayor de edad.', false, v => { st.adult = v; });
  const b = checkbox('He leído el texto y doy mi consentimiento.', false, v => { st.accept = v; });
  box.append(h('div', { class: 'statement' }, info.statement),
    notice('info', 'Los vídeos llevarán un aviso visible de contenido sintético. Si no está de acuerdo, simplemente cierre esta página: no se podrá usar su imagen ni su voz.'),
    field('Su nombre completo', name), a.el, b.el,
    h('div', { class: 'row end' }, h('button', { class: 'btn primary', onclick: async () => {
      try {
        const r = await api(`/api/consent/${token}/grant`, { method: 'POST', body: { full_name: name.value, accept: st.accept, confirm_adult: st.adult } });
        clear(box).append(h('h1', {}, 'Gracias'), notice('ok', r.message), revokeButton(token, box));
      } catch (e) { toast(e.message, 'error'); }
    } }, 'Doy mi consentimiento')));
}

function revokeButton(token, box) {
  return h('button', { class: 'btn danger', onclick: async () => {
    const r = await api(`/api/consent/${token}/revoke`, { method: 'POST' });
    clear(box).append(h('h1', {}, 'Consentimiento retirado'), notice('ok', r.message));
  } }, 'Retirar mi consentimiento');
}
