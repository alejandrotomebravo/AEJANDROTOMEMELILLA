// Paso 1: crear proyecto + declaración de uso responsable.
import { api, capabilities } from '../api.js';
import { h, field, select, checkbox, toast, notice } from '../ui.js';

const ASPECTS = [
  ['9:16', 'Vertical', 'Redes sociales, móvil', 27, 48],
  ['16:9', 'Horizontal', 'YouTube, televisión', 48, 27],
  ['1:1', 'Cuadrado', 'Publicaciones', 38, 38],
];

export async function render(root) {
  const caps = await capabilities();
  let aspect = '9:16';
  const name = h('input', { type: 'text', maxlength: 120, placeholder: 'Mi vídeo', required: true });
  const aspectBox = h('div', { class: 'aspects' });
  const drawAspects = () => {
    aspectBox.replaceChildren(...ASPECTS.map(([v, t, d, w, hh]) => h('button', {
      type: 'button', class: `aspect ${v === aspect ? 'sel' : ''}`, 'aria-pressed': v === aspect,
      onclick: () => { aspect = v; drawAspects(); },
    }, h('div', { class: 'shape', style: { width: `${w}px`, height: `${hh}px` } }), h('strong', {}, `${t} ${v}`),
    h('div', {}, h('small', { class: 'muted' }, d)))));
  };
  drawAspects();
  const res = select(caps.export.resolutions.map(r => [r, r === '2160p' ? '4K (2160p)' : r]), '1080p');
  const fps = select(caps.export.fps.map(f => [f, `${f} fps`]), 30);
  const codec = select([['h264', 'MP4 · H.264 (máxima compatibilidad)'], ...(caps.export.h265 ? [['h265', 'MP4 · H.265/HEVC (menor tamaño)']] : [])], 'h264');

  const purpose = select([['', 'Seleccione…'], ...Object.entries(caps.policy.purposes)], '');
  const detail = h('input', { type: 'text', maxlength: 500, placeholder: 'Ej.: felicitación de cumpleaños para mi hermana' });
  const distribution = select([['privado', 'Uso privado'], ['redes', 'Publicación en redes sociales'], ['comercial', 'Uso comercial']], 'privado');
  const confirms = {};
  const confirmEls = Object.entries(caps.policy.confirmations).map(([k, txt]) => {
    const c = checkbox(txt, false, v => { confirms[k] = v; });
    return c.el;
  });

  const submit = h('button', { class: 'btn primary', type: 'submit' }, 'Crear proyecto y continuar');
  root.append(h('h1', {}, 'Nuevo proyecto'), h('form', { onsubmit: async (e) => {
    e.preventDefault();
    if (!purpose.value) return toast('Seleccione la finalidad del vídeo.', 'warn');
    submit.disabled = true;
    try {
      const p = await api('/api/projects', { method: 'POST', body: { name: name.value, aspect, resolution: res.value, fps: Number(fps.value), codec: codec.value } });
      await api(`/api/projects/${p.id}/use-declaration`, { method: 'PUT', body: { purpose: purpose.value, detail: detail.value, distribution: distribution.value, confirmations: confirms } });
      location.hash = `#/proyecto/${p.id}/importar`;
    } catch (err) {
      toast(err.message, 'error');
    } finally { submit.disabled = false; }
  } },
  h('div', { class: 'cols even' },
    h('div', { class: 'card' }, h('h2', {}, 'Proyecto'), field('Nombre', name), h('div', { class: 'field' }, h('span', { class: 'label' }, 'Formato'), aspectBox),
      h('div', { class: 'cols even' }, field('Resolución de salida', res), field('Fotogramas por segundo', fps)), field('Formato de archivo', codec,
        'Puede cambiarlo al exportar.')),
    h('div', { class: 'card' }, h('h2', {}, 'Uso responsable'),
      field('Finalidad', purpose), field('Descripción breve (opcional)', detail), field('Difusión prevista', distribution),
      h('div', { class: 'field' }, h('span', { class: 'label' }, 'Compromisos (obligatorios)'), ...confirmEls),
      notice('info', 'Todos los vídeos llevarán la marca visible «VÍDEO EDITADO CON INTELIGENCIA ARTIFICIAL». Esta declaración queda registrada.'))),
  h('div', { class: 'row end' }, h('a', { class: 'btn ghost', href: '#/' }, 'Cancelar'), submit)));
}
