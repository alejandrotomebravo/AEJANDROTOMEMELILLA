import { api, assetUrl } from '../api.js';
import { h, fmtDate, confirmDialog, toast, spinner, clear } from '../ui.js';

export async function render(root) {
  root.append(h('div', { class: 'row', style: { marginTop: 0 } }, h('h1', {}, 'Proyectos recientes'), h('span', { class: 'spacer' }),
    h('a', { class: 'btn primary', href: '#/nuevo' }, '+ Nuevo proyecto')));
  const grid = h('div', { class: 'grid' }, spinner());
  root.append(grid);
  const projects = await api('/api/projects');
  clear(grid);
  grid.append(h('a', { class: 'card new-card', href: '#/nuevo' }, '+ Crear un proyecto'));
  for (const p of projects) {
    const thumb = h('div', { class: 'thumb' }, p.has_video ? '🎬' : '📁');
    if (p.thumb_asset_id) {
      assetUrl(p.thumb_asset_id).then(u => { thumb.textContent = ''; thumb.append(h('img', { src: u, alt: '' })); }).catch(() => {});
    }
    const del = h('button', { class: 'btn small danger', onclick: async (e) => {
      e.stopPropagation();
      if (!await confirmDialog('Eliminar proyecto', `Se borrarán «${p.name}» y todos sus archivos de forma permanente.`, 'Eliminar', 'danger')) return;
      await api(`/api/projects/${p.id}`, { method: 'DELETE' });
      toast('Proyecto eliminado', 'ok');
      card.remove();
    } }, 'Eliminar');
    const card = h('div', { class: 'card project-card', onclick: () => { location.hash = `#/proyecto/${p.id}/${p.has_video ? 'editor' : 'importar'}`; } },
      thumb, h('strong', {}, p.name),
      h('div', { class: 'row', style: { marginTop: 0 } }, h('span', { class: 'pill' }, p.aspect),
        h('small', { class: 'muted' }, fmtDate(p.updated_at)), h('span', { class: 'spacer' }), del));
    grid.append(card);
  }
  if (!projects.length) {
    root.append(h('div', { class: 'notice info' }, 'Aún no tiene proyectos. El asistente le guiará: vídeo → rostros → diálogo → sincronización labial → exportación con marca de agua.'));
  }
}
