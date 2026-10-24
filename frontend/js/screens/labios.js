// Paso 6: sincronización labial con comprobación previa de calidad y coste.
import { api, capabilities, ApiError, assetUrl } from '../api.js';
import { h, field, select, toast, notice, fmtTime, fmtDuration, clear, checkbox, confirmDialog, fmtMoney } from '../ui.js';
import { trackJob, videoPlayer, qualityReport } from '../components.js';

export async function render(root, project) {
  if (!project.has_video) { root.append(notice('warn', 'Importe primero un vídeo.')); return; }
  const caps = await capabilities(true);
  const ls = caps.lipsync;
  const [dialogues, characters] = await Promise.all([api(`/api/projects/${project.id}/dialogues`), api(`/api/projects/${project.id}/characters`)]);
  const sources = Object.fromEntries(project.sources.map(s => [s.id, s]));
  const ready = dialogues.filter(d => d.audio_asset_id);
  root.append(ls.available
    ? notice('info', h('strong', {}, `Motor: ${ls.provider === 'sync_so' ? 'sync.so (remoto, de pago)' : 'modelo local (gratuito)'}. `),
      ls.paid ? `Coste aproximado: ${ls.cost_per_second.toFixed(3)} USD por segundo. Se pedirá confirmación y sólo se envía el fragmento necesario.` : (ls.license || ''))
    : notice('warn', h('strong', {}, 'Sincronización labial no disponible en este servidor. '), ls.reason));
  if (!ready.length) {
    root.append(notice('warn', 'No hay diálogos con audio. ', h('a', { href: `#/proyecto/${project.id}/dialogos` }, 'Crear un diálogo')));
    return;
  }
  if (!characters.length) { root.append(notice('warn', 'No hay rostros detectados en los vídeos.')); return; }

  const player = h('video', { class: 'player', controls: true, playsinline: true, preload: 'metadata' });
  const setPlayer = async (sid) => { const s = sources[sid]; player.src = await assetUrl(s.review_asset_id || s.proxy_asset_id); };
  const dSel = select(ready.map(d => [d.id, `${(d.text || d.mode).replace(/\[pausa[^\]]*\]/g, '…').slice(0, 50)} · ${fmtDuration(d.duration)}`]), ready[0].id, () => check());
  const cSel = select(characters.map(c => [c.id, `${c.label} · ${c.source_label}`]), characters[0].id, async () => { setDefaultStart(); await setPlayer(cur().source_id); check(); });
  const cur = () => characters.find(x => x.id === cSel.value);
  const start = h('input', { type: 'number', min: 0, step: 0.1, value: 0, onchange: () => check() });
  const presence = h('div', { class: 'row' });
  const setDefaultStart = () => {
    const c = cur();
    clear(presence).append(h('small', { class: 'muted' }, 'Aparece en: '),
      ...(c.presence || []).slice(0, 8).map(r => h('button', { class: 'btn small ghost', onclick: () => { start.value = r.start; player.currentTime = r.start; check(); } }, `${fmtTime(r.start)}–${fmtTime(r.end)}`)));
    start.value = c.presence?.[0]?.start ?? 0;
  };
  setDefaultStart();
  await setPlayer(cur().source_id);
  let replaceAudio = true;
  const repl = checkbox('Silenciar el audio original durante el diálogo', true, v => { replaceAudio = v; });
  let accept = false;
  const acceptBox = checkbox('He revisado las advertencias y quiero continuar.', false, v => { accept = v; });
  acceptBox.el.hidden = true;
  const report = h('div');
  const jobBox = h('div');
  const out = h('div');
  const historyBox = h('div');
  let last = null;

  async function check() {
    clear(report);
    try {
      last = await api(`/api/projects/${project.id}/lipsync/check`, { method: 'POST', body: body(false) });
      const est = last.estimate;
      report.append(qualityReport(last), est?.available ? h('div', { class: 'row' },
        h('span', { class: `pill ${est.paid ? 'paid' : 'free'}` }, est.paid ? `Remoto · ≈ ${fmtMoney(est.estimated_cost_usd)}` : 'Local · gratuito'),
        h('span', { class: 'pill' }, `Tiempo ≈ ${fmtDuration(est.estimated_seconds)}`)) : '');
      acceptBox.el.hidden = !last.warnings.length;
    } catch (e) { report.append(notice('error', e.message)); }
  }
  const body = (preview) => ({ dialogue_id: dSel.value, character_id: cSel.value, source_start: Number(start.value), preview, accept_warnings: accept, replace_audio: replaceAudio });

  async function run(preview) {
    if (!last) await check();
    if (last?.blocking?.length) return toast('El material no permite una sincronización aceptable. Elija otro tramo o personaje, o ajuste el diálogo.', 'error');
    if (last?.warnings?.length && !accept) return toast('Revise y acepte las advertencias de calidad.', 'warn');
    let payload = body(preview);
    const est = last?.estimate;
    if (est?.paid) {
      if (!await confirmDialog('Procesamiento remoto de pago', `Coste estimado: ${fmtMoney(est.estimated_cost_usd)} (${preview ? 'vista previa de 4 s' : 'diálogo completo'}). El fragmento se enviará a sync.so mediante un enlace temporal.`, 'Confirmar y procesar')) return;
      payload = { ...payload, confirm_cost: true };
    }
    try {
      const r = await api(`/api/projects/${project.id}/lipsync`, { method: 'POST', body: payload });
      const j = await trackJob(jobBox, r.job, { title: preview ? 'Vista previa de sincronización' : 'Sincronización labial', estimatedSeconds: r.estimate?.estimated_seconds });
      if (j.status !== 'succeeded') return;
      const post = j.result.post_check;
      clear(out).append(post && !post.ok ? notice('warn', h('strong', {}, 'Revise el resultado: '), post.issues.join(' ')) : notice('ok', preview ? 'Vista previa lista.' : 'Diálogo sincronizado e incorporado a la línea de tiempo.'),
        await videoPlayer(preview ? j.result.preview_asset_id : j.result.review_asset_id),
        h('small', { class: 'muted' }, 'La sincronización depende del modelo y del material: no es perfecta. Si no le convence, restaure la versión anterior y repita.'));
      if (!preview) { toast('Puede ajustar volúmenes y tiempos en el editor.', 'ok'); drawHistory(); }
    } catch (e) {
      if (e instanceof ApiError && e.details?.report) clear(report).append(qualityReport(e.details.report));
      toast(e.message, 'error');
    }
  }

  async function drawHistory() {
    const list = await api(`/api/projects/${project.id}/sources`);
    const names = { faceswap: 'Sustitución facial', lipsync: 'Sincronización labial' };
    clear(historyBox).append(h('div', { class: 'card' }, h('h2', {}, 'Versiones de cada vídeo'),
      h('p', { class: 'muted' }, 'Si un resultado presenta artefactos, vuelva a una versión anterior y repita el procesamiento.'),
      ...list.filter(s => s.status === 'ready').map(s => h('div', {}, h('h3', {}, s.label), h('div', { class: 'list' },
        h('div', { class: 'list-item' }, h('span', { class: 'grow' }, 'Original'), !s.processed ? h('span', { class: 'pill ok' }, 'actual') : restoreBtn(project, s, -1)),
        s.history.map(v => h('div', { class: 'list-item' }, h('span', { class: 'grow' }, `${v.index + 1}. ${names[v.operation] || v.operation}`),
          v.current ? h('span', { class: 'pill ok' }, 'actual') : restoreBtn(project, s, v.index))))))));
  }

  root.append(h('div', { class: 'cols' },
    h('div', { class: 'card' }, player,
      h('div', { class: 'row' }, h('button', { class: 'btn small', onclick: () => { start.value = player.currentTime.toFixed(2); check(); } }, 'Empezar el diálogo aquí'))),
    h('div', { class: 'card' }, h('h2', {}, 'Sincronizar'), field('Diálogo', dSel), field('Personaje que habla', cSel),
      field('Inicio en su vídeo (s)', start), presence, repl.el, report, acceptBox.el,
      h('div', { class: 'row' },
        h('button', { class: 'btn', disabled: !ls.available, onclick: () => run(true) }, 'Vista previa (4 s)'),
        h('button', { class: 'btn primary', disabled: !ls.available, onclick: () => run(false) }, 'Sincronizar diálogo')),
      jobBox, out)),
  historyBox);
  drawHistory();
  check();
}

function restoreBtn(project, s, index) {
  return h('button', { class: 'btn small', onclick: async () => {
    if (!await confirmDialog('Restaurar versión', `«${s.label}» volverá a esta versión. Los diálogos colocados en la línea de tiempo se mantienen.`, 'Restaurar')) return;
    const r = await api(`/api/projects/${project.id}/sources/${s.id}/history/${index}/restore`, { method: 'POST' });
    const box = h('div');
    document.querySelector('.step-body').prepend(box);
    await trackJob(box, r.job, { title: 'Generando copia de revisión' });
    location.reload();
  } }, 'Restaurar');
}
