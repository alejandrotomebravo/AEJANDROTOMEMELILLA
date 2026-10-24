// Pantalla 12: gestión de proyectos (espacio, caducidad, duplicar, renombrar, eliminar).
import { api } from '../api.js';
import { h, fmtDate, fmtBytes, confirmDialog, toast, spinner, clear } from '../ui.js';

export async function render(root) {
  const box = h('div', {}, spinner());
  const filter = h('input', { type: 'search', placeholder: 'Buscar por nombre', oninput: () => draw() });
  root.append(h('div', { class: 'row', style: { marginTop: 0 } }, h('h1', {}, 'Gestión de proyectos'), h('span', { class: 'spacer' }),
    h('a', { class: 'btn primary', href: '#/nuevo' }, '+ Nuevo')), filter, box);
  let projects = await api('/api/projects');
  function draw() {
    const q = filter.value.trim().toLowerCase();
    const list = projects.filter(p => !q || p.name.toLowerCase().includes(q));
    const total = projects.reduce((a, p) => a + (p.storage_bytes || 0), 0);
    clear(box).append(h('p', { class: 'muted' }, `${projects.length} proyectos · ${fmtBytes(total)} ocupados`),
      h('div', { class: 'list' }, list.map(p => h('div', { class: 'list-item' },
        h('div', { class: 'grow' }, h('a', { href: `#/proyecto/${p.id}/${p.has_video ? 'escenas' : 'personajes'}` }, h('strong', {}, p.name)),
          h('div', {}, h('small', { class: 'muted' }, `${p.aspect} · ${p.cast} personaje(s) · ${p.clips} clip(s) · ${p.exports} exportación(es) · ${fmtBytes(p.storage_bytes)} · modificado ${fmtDate(p.updated_at)}`)),
          p.expires_at ? h('div', {}, h('small', { class: 'muted' }, `Se eliminará automáticamente el ${fmtDate(p.expires_at)} si no hay actividad.`)) : null),
        h('button', { class: 'btn small', onclick: async () => {
          const name = prompt('Nuevo nombre', p.name);
          if (!name) return;
          await api(`/api/projects/${p.id}`, { method: 'PATCH', body: { name } });
          p.name = name; draw();
        } }, 'Renombrar'),
        h('button', { class: 'btn small', onclick: async () => {
          await api(`/api/projects/${p.id}/duplicate`, { method: 'POST' });
          projects = await api('/api/projects'); toast('Proyecto duplicado (instrucciones y ajustes, sin personajes ni clips)', 'ok'); draw();
        } }, 'Duplicar'),
        h('button', { class: 'btn small danger', onclick: async () => {
          if (!await confirmDialog('Eliminar proyecto', `Se borrarán «${p.name}», sus personajes, escenas, clips y exportaciones.`, 'Eliminar', 'danger')) return;
          await api(`/api/projects/${p.id}`, { method: 'DELETE' });
          projects = projects.filter(x => x.id !== p.id); toast('Proyecto eliminado', 'ok'); draw();
        } }, 'Eliminar')))));
  }
  draw();
}
