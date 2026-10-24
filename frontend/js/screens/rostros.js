// Paso 4: personajes detectados → identidad autorizada → vista previa → procesamiento.
import { api, capabilities } from '../api.js';
import { h, field, select, toast, notice, fmtTime, fmtDuration, clear, confirmDialog } from '../ui.js';
import { trackJob, videoPlayer, img } from '../components.js';
import { identityManager } from './identidades.js';

export async function render(root, project) {
  if (!project.has_video) {
    root.append(notice('warn', 'Importe primero un vídeo. ', h('a', { href: `#/proyecto/${project.id}/internet` }, 'Ir a importar')));
    return;
  }
  const caps = await capabilities();
  const fs = caps.face_swap;
  if (!fs.available) {
    root.append(notice('warn', h('strong', {}, 'Sustitución facial no disponible en este servidor. '), fs.reason,
      ' Puede continuar con diálogos y sincronización labial sobre el rostro original si cuenta con autorización.'));
  } else if (fs.license) {
    root.append(notice('info', `Motor: ${fs.provider} · ${fs.license}`));
  }
  const characters = await api(`/api/projects/${project.id}/characters`);
  if (!characters.length) {
    root.append(notice('warn', 'No se detectaron rostros en los vídeos del proyecto.'));
    return;
  }
  let identities = [];
  let selected = characters[0];
  const facesBox = h('div', { class: 'faces' });
  const detail = h('div');
  const idsBox = h('div');

  async function drawFaces() {
    clear(facesBox);
    for (const c of characters) {
      const im = c.thumb_asset_id ? await img(c.thumb_asset_id, c.label) : h('div');
      const ident = identities.find(i => i.id === c.identity_id);
      facesBox.append(h('button', { class: `face ${c.id === selected.id ? 'sel' : ''}`, onclick: () => { selected = c; drawFaces(); drawDetail(); } },
        im, h('strong', {}, c.label), h('div', {}, h('small', { class: 'muted' }, c.source_label)),
        h('div', {}, ident ? h('span', { class: 'pill ok' }, `→ ${ident.display_name}`) : h('span', { class: 'pill' }, 'Sin asignar'))));
    }
  }

  async function drawDetail() {
    const c = selected;
    const usable = identities.filter(i => ['verified', 'verified_self'].includes(i.consent_status) && i.scopes.includes('imagen') && i.photos.length);
    const sel = select([['', 'Sin asignar (rostro original)'], ...usable.map(i => [i.id, i.display_name])], c.identity_id || '', async (v) => {
      try {
        await api(`/api/projects/${project.id}/characters/${c.id}`, { method: 'PATCH', body: v ? { identity_id: v } : { clear_identity: true } });
        c.identity_id = v || null;
        toast(v ? 'Identidad asignada' : 'Asignación eliminada', 'ok');
        drawFaces(); drawDetail();
      } catch (e) { toast(e.message, 'error'); sel.value = c.identity_id || ''; }
    });
    const label = h('input', { type: 'text', value: c.label, maxlength: 40, onchange: async () => {
      await api(`/api/projects/${project.id}/characters/${c.id}`, { method: 'PATCH', body: { label: label.value } });
      c.label = label.value; drawFaces();
    } });
    const ranges = c.presence || [];
    const startSel = select(ranges.map(r => [r.start, `${fmtTime(r.start)} – ${fmtTime(r.end)}`]), ranges[0]?.start);
    const intOut = h('output', {}, '100 %');
    const intensity = h('input', { type: 'range', min: 0.3, max: 1, step: 0.05, value: 1, oninput: () => { intOut.textContent = `${Math.round(intensity.value * 100)} %`; } });
    const jobBox = h('div');
    const out = h('div');
    const canRun = fs.available && c.identity_id;
    const run = async (preview) => {
      const body = { character_id: c.id, intensity: Number(intensity.value) };
      if (preview) Object.assign(body, { preview_start: Number(startSel.value), preview_duration: 3 });
      else {
        const secs = (c.stats.last - c.stats.first) + 1;
        if (!await confirmDialog('Procesar todo el tramo', `Se sustituirá el rostro en todo el tramo en que aparece (${fmtDuration(secs)}). Tiempo estimado: ${fmtDuration(secs * fs.seconds_per_second)} (procesamiento local, sin coste por uso). Si aparecen artefactos podrá volver a la versión anterior y repetirlo.`, 'Procesar')) return;
      }
      try {
        const r = await api(`/api/projects/${project.id}/faceswap`, { method: 'POST', body });
        const j = await trackJob(jobBox, r.job, { title: preview ? 'Vista previa' : 'Sustitución facial', estimatedSeconds: r.estimated_seconds });
        if (j.status !== 'succeeded') return;
        (j.result.warnings || []).forEach(w => toast(w, 'warn', 8000));
        clear(out).append(preview ? notice('info', 'Vista previa de 3 s. Ajuste la intensidad y repita si es necesario.')
          : notice('ok', 'Rostro sustituido. La etiqueta «ROSTROS ALTERADOS CON IA» se añadirá a la marca de agua.'),
        await videoPlayer(preview ? j.result.preview_asset_id : j.result.review_asset_id));
      } catch (e) { toast(e.message, 'error'); }
    };
    clear(detail).append(h('div', { class: 'card' },
      h('h2', {}, c.label), h('small', { class: 'muted' }, `En «${c.source_label}»`), field('Etiqueta', label),
      field('Identidad autorizada', sel, usable.length ? 'Sólo aparecen personas con consentimiento de imagen verificado y al menos una foto.' : 'Añada a la derecha una persona con consentimiento y fotos.'),
      h('dl', { class: 'kv' }, h('dt', {}, 'Aparece'), h('dd', {}, `${ranges.length} tramos · ${c.stats.seconds_visible ?? '?'} s`),
        h('dt', {}, 'Tamaño del rostro'), h('dd', {}, `${c.stats.median_face_px} px`)),
      c.stats.median_face_px < 96 ? notice('warn', 'Rostro pequeño: la sustitución puede verse borrosa.') : null,
      field('Intensidad de la transformación', h('div', { class: 'range-row' }, intensity, intOut),
        'Valores bajos conservan más la expresión, la luz y los rasgos originales; valores altos, más parecido con la identidad.'),
      field('Fragmento para la vista previa', startSel),
      h('div', { class: 'row' },
        h('button', { class: 'btn', disabled: !canRun, onclick: () => run(true) }, 'Vista previa (3 s)'),
        h('button', { class: 'btn primary', disabled: !canRun, onclick: () => run(false) }, 'Procesar')),
      !canRun && fs.available ? h('small', { class: 'muted' }, 'Asigne una identidad para habilitar el procesamiento.') : null,
      notice('info', 'Si aparecen artefactos, use «Versiones» en el paso «Labios» para volver atrás y repita con otra intensidad o mejores fotos (varias perspectivas).'),
      jobBox, out));
  }

  root.append(h('div', { class: 'cols' }, h('div', {}, h('div', { class: 'card' }, h('h2', {}, 'Personajes'), facesBox), detail),
    h('div', { class: 'card' }, h('h2', {}, 'Personas autorizadas'), idsBox)));
  await identityManager(idsBox, (ids) => { identities = ids; drawFaces(); drawDetail(); });
  root.append(h('div', { class: 'row end' }, h('a', { class: 'btn primary', href: `#/proyecto/${project.id}/dialogos` }, 'Continuar con diálogos →')));
}
