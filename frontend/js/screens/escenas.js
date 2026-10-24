// Paso 3: por escena, fotograma clave (imagen con los personajes y el estilo) → clip de vídeo → voces.
import { api, capabilities } from '../api.js';
import { h, toast, notice, clear, fmtDuration, spinner } from '../ui.js';
import { trackJob, generate, providerNote, img, videoPlayer } from '../components.js';

export async function render(root, project) {
  const caps = await capabilities();
  if (!project.storyboard.scenes.length) {
    root.append(notice('warn', 'Todavía no hay guion. ', h('a', { href: `#/proyecto/${project.id}/guion` }, 'Escríbalo primero'), '.'));
    return;
  }
  const summary = h('div', { class: 'card' }, spinner());
  const list = h('div', { class: 'list scenes' });
  const pendingPortraits = project.cast.filter(c => c.status !== 'ready');

  async function reload() {
    Object.assign(project, await api(`/api/projects/${project.id}`));
  }

  async function drawSummary() {
    const e = await api(`/api/projects/${project.id}/generation-estimate`);
    const all = h('button', { class: 'btn primary', disabled: !e.pending.keyframes && !e.pending.clips, onclick: () => generateAll(e) }, 'Generar todo lo pendiente');
    clear(summary).append(h('h2', {}, 'Producción'),
      h('dl', { class: 'kv' },
        h('dt', {}, 'Pendiente'), h('dd', {}, `${e.pending.keyframes} fotogramas · ${e.pending.clips} clips${e.pending.portraits ? ` · ${e.pending.portraits} retratos` : ''}`),
        h('dt', {}, 'Coste'), h('dd', {}, 'Gratuito'),
        h('dt', {}, 'Espera aprox.'), h('dd', {}, fmtDuration(e.estimated_wait_seconds)),
        h('dt', {}, 'Duración del vídeo'), h('dd', {}, fmtDuration(e.video_seconds)),
        h('dt', {}, 'Procesamiento'), h('dd', {}, 'En su servidor (filtros, composición y animática)')),
      all);
  }

  async function generateAll(e) {
    if (pendingPortraits.length) return toast(`Genere primero los retratos de: ${pendingPortraits.map(c => c.name).join(', ')}.`, 'warn');
    for (const s of project.storyboard.scenes) {
      const card = cards.get(s.id);
      try {
        if (!s.keyframe) {
          const r = await api(`/api/projects/${project.id}/scenes/${s.id}/keyframe`, { method: 'POST' });
          const j = await trackJob(card.jobBox, r.job, { title: `Fotograma: ${s.title}` });
          if (j.status !== 'succeeded') break;
          await reload();
        }
        const cur = project.storyboard.scenes.find(x => x.id === s.id);
        if (!cur.clip) {
          const r = await api(`/api/projects/${project.id}/scenes/${s.id}/animate`, { method: 'POST' });
          const j = await trackJob(card.jobBox, r.job, { title: `Clip: ${s.title}`, estimatedSeconds: r.estimate.estimated_wait_seconds });
          if (j.status !== 'succeeded') break;
          await reload();
        }
      } catch (err) { toast(err.message, 'error'); break; }
    }
    await drawAll();
  }

  const cards = new Map();
  async function sceneCard(s, i) {
    const jobBox = h('div');
    const kf = h('div', { class: 'keyframe' }, s.keyframe ? await img(s.keyframe.preview_asset_id, `Fotograma de ${s.title}`) : h('span', { class: 'ph' }, 'Sin fotograma'));
    const clip = h('div', { class: 'clip' }, s.clip ? await videoPlayer(s.clip.preview_asset_id) : h('span', { class: 'ph' }, 'Sin clip'));
    const run = async (btn, path, title, est) => {
      btn.disabled = true;
      try {
        const r = await generate(path);
        const j = await trackJob(jobBox, r.jobs ? r.jobs[0] : r.job, { title, estimatedSeconds: r.estimate?.estimated_wait_seconds ?? est });
        if (r.jobs) for (const other of r.jobs.slice(1)) await trackJob(jobBox, other, { title });
        if (j.status === 'succeeded') { await reload(); await drawAll(); }
      } catch (e) { toast(e.message, 'error'); } finally { btn.disabled = false; }
    };
    const kfBtn = h('button', { class: 'btn small', onclick: () => run(kfBtn, `/api/projects/${project.id}/scenes/${s.id}/keyframe`, `Fotograma: ${s.title}`, 3) },
      s.keyframe ? 'Regenerar fotograma' : '1 · Generar fotograma');
    const clipBtn = h('button', { class: 'btn small primary', disabled: !s.keyframe, title: s.keyframe ? '' : 'Genere antes el fotograma',
      onclick: () => run(clipBtn, `/api/projects/${project.id}/scenes/${s.id}/animate`, `Clip: ${s.title}`) },
    s.clip ? 'Regenerar clip' : '2 · Animar en vídeo');
    const voiceBtn = s.dialogue.length ? h('button', { class: 'btn small', onclick: async () => {
      voiceBtn.disabled = true;
      try {
        const r = await api(`/api/projects/${project.id}/scenes/${s.id}/voices`, { method: 'POST' });
        for (const j of r.jobs) await trackJob(jobBox, j, { title: 'Voces de la escena' });
        toast(s.clip ? 'Voces y subtítulos añadidos al montaje' : 'Voces creadas: se colocarán al generar el clip', 'ok');
      } catch (e) { toast(e.message, 'error'); } finally { voiceBtn.disabled = false; }
    } }, '3 · Crear voces') : null;
    const card = h('div', { class: 'card scene-card' },
      h('div', { class: 'row', style: { marginTop: 0 } }, h('span', { class: 'pill' }, `Escena ${i + 1}`), h('strong', {}, s.title),
        h('span', { class: 'spacer' }), h('small', { class: 'muted' }, `${s.duration_seconds} s · ${s.characters.join(', ') || 'sin personajes'}`)),
      h('p', { class: 'muted' }, s.description),
      s.dialogue.length ? h('ul', { class: 'small-list' }, s.dialogue.map(d => h('li', {}, h('strong', {}, `${d.character}: `), d.line))) : null,
      h('div', { class: 'compare' }, h('figure', {}, h('figcaption', {}, 'Fotograma clave'), kf), h('figure', {}, h('figcaption', {}, 'Clip'), clip)),
      s.clip ? h('small', { class: 'muted' }, `Clip de ${s.clip.seconds} s · animática con movimiento de cámara`) : null,
      h('div', { class: 'row' }, kfBtn, clipBtn, voiceBtn), jobBox);
    cards.set(s.id, { jobBox });
    return card;
  }

  async function drawAll() {
    clear(list);
    for (const [i, s] of project.storyboard.scenes.entries()) list.append(await sceneCard(s, i));
    await drawSummary();
  }

  root.append(providerNote(),
    pendingPortraits.length ? notice('warn', `Faltan retratos de: ${pendingPortraits.map(c => c.name).join(', ')}. `, h('a', { href: `#/proyecto/${project.id}/personajes` }, 'Genérelos en «Personajes»'), '.') : '',
    summary, list,
    notice('info', 'Todas las imágenes y clips se muestran con el aviso «VÍDEO GENERADO CON INTELIGENCIA ARTIFICIAL». Los clips terminados se colocan solos en el montaje, en el orden del guion.'),
    h('div', { class: 'row end' }, h('a', { class: 'btn primary', href: `#/proyecto/${project.id}/editor` }, 'Montar en el editor →')));
  await drawAll();
}
