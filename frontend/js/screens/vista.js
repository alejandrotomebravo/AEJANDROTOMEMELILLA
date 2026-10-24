// Paso 7: vista previa exacta renderizada en el servidor y comparación con el original.
import { api, assetUrl } from '../api.js';
import { h, field, toast, notice, fmtTime, clear } from '../ui.js';
import { trackJob } from '../components.js';
import { outputDuration, outputToSource } from '../history.js';

export async function render(root, project) {
  if (!project.proxy_asset_id) { root.append(notice('warn', 'Importe primero un vídeo.')); return; }
  const tl = project.timeline;
  const total = outputDuration(tl);
  const start = h('input', { type: 'number', min: 0, max: total, step: 0.5, value: 0 });
  const end = h('input', { type: 'number', min: 0, max: total, step: 0.5, value: Math.min(total, 15).toFixed(1) });
  const jobBox = h('div');
  const out = h('div');
  const go = h('button', { class: 'btn primary', onclick: async () => {
    const a = Number(start.value); const b = Number(end.value);
    if (!(b - a >= 0.5)) return toast('El fragmento debe durar al menos 0,5 s.', 'warn');
    if (b - a > 60) return toast('La vista previa admite hasta 60 s. Exporte para ver el vídeo completo.', 'warn');
    go.disabled = true;
    try {
      const r = await api(`/api/projects/${project.id}/preview`, { method: 'POST', body: { start: a, end: b } });
      const j = await trackJob(jobBox, r.job, { title: 'Renderizando vista previa (360p)' });
      if (j.status === 'succeeded') await showCompare(out, project, j.result.preview_asset_id, a);
    } catch (e) { toast(e.message, 'error'); } finally { go.disabled = false; }
  } }, 'Generar vista previa');
  root.append(h('div', { class: 'card' }, h('h2', {}, 'Vista previa del montaje'),
    h('p', { class: 'muted' }, `Duración total del montaje: ${fmtTime(total)}. Se renderiza en baja resolución con subtítulos, gráficos y la marca de agua, exactamente como en la exportación.`),
    h('div', { class: 'cols even' }, field('Desde (s)', start), field('Hasta (s)', end)), go, jobBox),
  out,
  h('div', { class: 'row end' }, h('a', { class: 'btn primary', href: `#/proyecto/${project.id}/exportar` }, 'Exportar →')));
}

async function showCompare(container, project, previewId, offset) {
  const res = h('video', { class: 'player', controls: true, playsinline: true });
  const orig = h('video', { class: 'player', playsinline: true, muted: true });
  res.src = await assetUrl(previewId);
  orig.src = await assetUrl(project.proxy_asset_id);
  const sync = () => {
    const m = outputToSource(project.timeline, offset + res.currentTime);
    if (m && Math.abs(orig.currentTime - m.source) > 0.2) orig.currentTime = m.source;
  };
  res.addEventListener('play', () => { sync(); orig.play().catch(() => {}); });
  res.addEventListener('pause', () => orig.pause());
  res.addEventListener('seeked', sync);
  res.addEventListener('timeupdate', sync);
  clear(container).append(h('div', { class: 'card' }, h('h2', {}, 'Comparación'),
    h('div', { class: 'compare' }, h('figure', {}, h('figcaption', {}, 'Original (sin audio)'), orig), h('figure', {}, h('figcaption', {}, 'Resultado'), res)),
    h('small', { class: 'muted' }, 'Controle la reproducción desde el vídeo de la derecha; el original le sigue.')));
}
