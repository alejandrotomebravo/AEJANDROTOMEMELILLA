import { api, assetUrl } from '../api.js';
import { h, fmtDate, confirmDialog, toast, spinner, clear } from '../ui.js';

export async function render(root) {
  root.append(h('div', { class: 'card hero' }, h('h1', {}, 'Genere vídeos con IA a partir de instrucciones'),
    h('p', { class: 'muted' }, 'Elija el estilo (realista, caricatura, cómic, anime…), añada personajes con su cara o la de personas que lo autoricen —o inventados—, describa la historia y la IA escribe el guion, genera las escenas, las voces y el montaje con aviso de IA.'),
    h('div', { class: 'row' }, h('a', { class: 'btn primary', href: '#/nuevo' }, '+ Nuevo proyecto'), h('a', { class: 'btn', href: '#/proyectos' }, 'Gestionar proyectos'),
      h('a', { class: 'btn ghost', href: '#/identidades' }, 'Personas autorizadas'))),
  h('h2', {}, 'Proyectos recientes'));
  const grid = h('div', { class: 'grid' }, spinner());
  root.append(grid);
  const projects = await api('/api/projects');
  clear(grid);
  grid.append(h('a', { class: 'card new-card', href: '#/nuevo' }, '+ Crear un proyecto'));
  for (const p of projects.slice(0, 11)) {
    const thumb = h('div', { class: 'thumb' }, p.has_video ? '🎬' : '✨');
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
    const card = h('div', { class: 'card project-card', onclick: () => { location.hash = `#/proyecto/${p.id}/${p.has_video ? 'escenas' : 'personajes'}`; } },
      thumb, h('strong', {}, p.name),
      h('div', { class: 'row', style: { marginTop: 0 } }, h('span', { class: 'pill' }, p.aspect),
        h('small', { class: 'muted' }, fmtDate(p.updated_at)), h('span', { class: 'spacer' }), del));
    grid.append(card);
  }
  if (!projects.length) {
    root.append(h('div', { class: 'notice info' }, 'Aún no tiene proyectos. El asistente le guiará: personajes y estilo → instrucciones y guion → escenas → montaje → exportación con marca de agua.'));
  }
}
