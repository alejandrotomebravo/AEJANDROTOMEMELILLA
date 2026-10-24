// Paso 2: procedencia del material, importación, recorte y análisis.
import { api, upload, capabilities } from '../api.js';
import { h, field, checkbox, select, toast, notice, fmtTime, fmtDuration, clear, spinner } from '../ui.js';
import { trackJob, videoPlayer, img } from '../components.js';

export async function render(root, project) {
  const caps = await capabilities();
  if (!project.use_declaration) {
    root.append(notice('warn', 'Falta la declaración de uso responsable de este proyecto. ', h('a', { href: '#/nuevo' }, 'Cree el proyecto desde el asistente.')));
  }
  if (!project.provenance) return provenanceForm(root, project, caps);
  const pending = (await api(`/api/projects/${project.id}/jobs`)).find(j => ['queued', 'running'].includes(j.status) && ['import_video', 'analyze'].includes(j.type));
  if (pending) {
    const box = h('div', { class: 'card' });
    root.append(box);
    await followImport(box, pending, project);
    return;
  }
  if (!project.proxy_asset_id) return uploader(root, project, caps);
  return overview(root, project, caps);
}

function provenanceForm(root, project, caps) {
  const data = { source_type: '', attested: false };
  const types = h('div', { class: 'list' }, Object.entries(caps.policy.source_types).map(([k, label]) => h('label', { class: 'check list-item' },
    h('input', { type: 'radio', name: 'st', value: k, onchange: () => { data.source_type = k; extra.hidden = k === 'propio'; } }), h('span', {}, label))));
  const holder = h('input', { type: 'text', maxlength: 200, placeholder: 'Persona u organización titular' });
  const url = h('input', { type: 'url', placeholder: 'https://… enlace a la licencia o autorización' });
  const desc = h('input', { type: 'text', maxlength: 500, placeholder: 'Ej.: vídeo de la boda grabado por mi primo, que me ha dado permiso' });
  let evidence = null;
  const evInput = h('input', { type: 'file', accept: 'application/pdf,image/*', onchange: async () => {
    const fd = new FormData();
    fd.append('file', evInput.files[0]);
    try {
      const r = await upload(`/api/projects/${project.id}/evidence`, fd);
      evidence = r.asset_id;
      toast('Documento adjuntado', 'ok');
    } catch (e) { toast(e.message, 'error'); }
  } });
  const extra = h('div', { hidden: true },
    field('Titular de los derechos', holder), field('Enlace a la licencia', url, 'Para Creative Commons se comprueba automáticamente si permite obras derivadas.'),
    field('Documento de licencia o autorización (opcional)', evInput));
  const internet = checkbox('El vídeo lo he obtenido de Internet (redes sociales, webs, plataformas).', false, v => { data.internet_download = v; });
  const people = checkbox('En el vídeo aparecen otras personas además de las que yo autorizo en este proyecto.', false, v => { data.third_party_people = v; peopleOk.el.hidden = !v; });
  const peopleOk = checkbox('Cuento con la autorización de esas personas para alterar su imagen o su voz.', false, v => { data.third_party_people_consent = v; });
  peopleOk.el.hidden = true;
  const attest = checkbox('Declaro que dispongo de los derechos o autorizaciones necesarios para modificar este vídeo y difundir el resultado según la finalidad indicada.', false, v => { data.attested = v; });
  const btn = h('button', { class: 'btn primary', onclick: async () => {
    try {
      await api(`/api/projects/${project.id}/provenance`, { method: 'PUT', body: { ...data, rights_holder: holder.value, license_url: url.value, description: desc.value, evidence_asset_id: evidence } });
      toast('Procedencia registrada', 'ok');
      location.reload();
    } catch (e) { toast(e.message, 'error'); }
  } }, 'Guardar y continuar');
  root.append(h('div', { class: 'card' }, h('h2', {}, '¿De dónde procede el vídeo?'),
    notice('info', 'Que un vídeo esté disponible en Internet no significa que pueda modificarlo o redistribuirlo. Indique con qué derecho lo utiliza.'),
    types, extra, field('Descripción', desc), internet.el, people.el, peopleOk.el, attest.el, h('div', { class: 'row end' }, btn)));
}

function uploader(root, project, caps) {
  const box = h('div');
  const input = h('input', { type: 'file', accept: 'video/*,.mov,.mp4,.m4v,.webm,.mkv', hidden: true, onchange: () => send(input.files[0]) });
  const drop = h('div', { class: 'card new-card', style: { minHeight: '220px', cursor: 'pointer' }, onclick: () => input.click(),
    ondragover: (e) => e.preventDefault(), ondrop: (e) => { e.preventDefault(); send(e.dataTransfer.files[0]); } },
  h('div', { style: { textAlign: 'center' } }, h('div', { style: { fontSize: '2.4rem' } }, '⬆'), 'Seleccionar vídeo de la galería o del ordenador',
    h('div', {}, h('small', { class: 'muted' }, `MP4, MOV y otros formatos · máx. ${caps.limits.max_upload_mb} MB y ${fmtDuration(caps.limits.max_video_seconds)}`))));
  async function send(file) {
    if (!file) return;
    if (file.size > caps.limits.max_upload_mb * 1024 * 1024) return toast('El archivo supera el tamaño máximo.', 'error');
    const bar = h('div', { class: 'bar-fill' });
    const label = h('span', {}, 'Subiendo… 0 %');
    clear(box).append(h('div', { class: 'job' }, h('div', { class: 'job-head' }, h('strong', {}, file.name), label), h('div', { class: 'bar' }, bar)));
    try {
      const fd = new FormData();
      fd.append('file', file);
      const r = await upload(`/api/projects/${project.id}/source`, fd, p => { bar.style.width = `${p * 100}%`; label.textContent = `Subiendo… ${Math.round(p * 100)} %`; });
      await followImport(box, r.job, project);
    } catch (e) { toast(e.message, 'error'); clear(box); }
  }
  root.append(h('h2', {}, 'Importar vídeo'), drop, input, box,
    notice('info', 'Consejo: los mejores resultados se obtienen con rostros de frente, bien iluminados y de al menos 128 píxeles de alto.'));
}

async function followImport(box, job, project) {
  const j = await trackJob(box, job, { title: job.type === 'analyze' ? 'Análisis de rostros y audio' : 'Importando vídeo' });
  if (j.status !== 'succeeded') return;
  if (j.type === 'import_video' && j.result?.analysis_job_id) {
    const a = await api(`/api/jobs/${j.result.analysis_job_id}`);
    const j2 = await trackJob(box, a, { title: 'Análisis de rostros y audio' });
    if (j2.status !== 'succeeded') return;
  }
  toast('Vídeo listo', 'ok');
  location.reload();
}

async function overview(root, project, caps) {
  const src = project.source;
  const player = await videoPlayer(project.proxy_asset_id);
  const clips = project.timeline.video.clips;
  const trimmed = clips.length === 1;
  const inT = h('input', { type: 'number', step: '0.1', min: 0, max: src.duration, value: trimmed ? clips[0].in : 0 });
  const outT = h('input', { type: 'number', step: '0.1', min: 0, max: src.duration, value: trimmed ? clips[0].out : src.duration });
  const setFromPlayer = (inp) => h('button', { class: 'btn small ghost', type: 'button', onclick: () => { inp.value = player.currentTime.toFixed(2); } }, 'Usar posición actual');
  const saveTrim = h('button', { class: 'btn', onclick: async () => {
    const a = Number(inT.value); const b = Number(outT.value);
    if (!(b - a >= 0.5)) return toast('El tramo debe durar al menos 0,5 s.', 'warn');
    const tl = structuredClone(project.timeline);
    tl.video.clips = [{ id: 'principal', in: a, out: b }];
    try {
      const r = await api(`/api/projects/${project.id}/timeline`, { method: 'PUT', body: { version: project.timeline_version, timeline: tl } });
      project.timeline = r.timeline; project.timeline_version = r.version;
      toast('Recorte aplicado', 'ok');
    } catch (e) { toast(e.message, 'error'); }
  } }, 'Aplicar recorte');

  const an = project.analysis || {};
  const scenes = an.scenes || [];
  const splitScenes = h('button', { class: 'btn small', disabled: !scenes.length, onclick: async () => {
    const cuts = [0, ...scenes.filter(s => s > 0.3 && s < src.duration - 0.3), src.duration];
    const tl = structuredClone(project.timeline);
    tl.video.clips = cuts.slice(0, -1).map((s, i) => ({ id: `esc${i + 1}`, in: s, out: cuts[i + 1] })).filter(c => c.out - c.in >= 0.1);
    try {
      const r = await api(`/api/projects/${project.id}/timeline`, { method: 'PUT', body: { version: project.timeline_version, timeline: tl } });
      project.timeline_version = r.version;
      toast(`${tl.video.clips.length} escenas creadas: puede reordenarlas en el editor`, 'ok');
    } catch (e) { toast(e.message, 'error'); }
  } }, 'Dividir en escenas');

  const facesBox = h('div', { class: 'faces' });
  for (const c of project.characters) {
    const im = c.thumb_asset_id ? await img(c.thumb_asset_id, c.label) : h('div');
    facesBox.append(h('div', { class: 'face' }, im, h('strong', {}, c.label),
      h('div', {}, h('small', { class: 'muted' }, `${c.stats.seconds_visible ?? '?'} s visible · ${c.stats.median_face_px} px`))));
  }
  const speech = an.speech_segments || [];
  root.append(h('div', { class: 'cols' },
    h('div', { class: 'card' }, player,
      h('dl', { class: 'kv', style: { marginTop: '10px' } }, h('dt', {}, 'Duración'), h('dd', {}, fmtDuration(src.duration)),
        h('dt', {}, 'Resolución'), h('dd', {}, `${src.width}×${src.height} · ${Math.round(src.fps || 0)} fps`),
        h('dt', {}, 'Procedencia'), h('dd', {}, caps.policy.source_types[project.provenance.source_type])),
      ...(project.provenance.warnings || []).map(w => notice('warn', w)),
      h('h3', { style: { marginTop: '14px' } }, 'Recorte de inicio y final'),
      h('div', { class: 'cols even' }, field('Inicio (s)', h('div', {}, inT, setFromPlayer(inT))), field('Final (s)', h('div', {}, outT, setFromPlayer(outT)))),
      h('div', { class: 'row' }, saveTrim, h('button', { class: 'btn ghost', onclick: () => uploaderReplace(root, project, caps) }, 'Reemplazar vídeo'))),
    h('div', {},
      h('div', { class: 'card' }, h('h2', {}, `Rostros detectados (${project.characters.length})`),
        project.characters.length ? facesBox : notice('warn', 'No se han detectado rostros. La sustitución facial y la sincronización labial necesitan rostros visibles.'),
        h('small', { class: 'muted' }, `Detector: ${an.detector === 'yunet' ? 'YuNet (con puntos faciales)' : 'Haar (básico)'}`),
        h('div', { class: 'row' }, h('a', { class: 'btn primary', href: `#/proyecto/${project.id}/rostros` }, 'Asignar rostros →'))),
      h('div', { class: 'card' }, h('h2', {}, 'Escenas y audio'),
        h('p', {}, `${scenes.length} cambios de escena detectados.`), splitScenes,
        h('h3', { style: { marginTop: '14px' } }, `Actividad de audio (${speech.length} tramos)`),
        h('div', { class: 'list' }, speech.slice(0, 12).map(s => h('button', { class: 'list-item btn ghost small', onclick: () => { player.currentTime = s.start; player.play(); } },
          `${fmtTime(s.start)} – ${fmtTime(s.end)}`))),
        h('small', { class: 'muted' }, 'Detección por energía: incluye voz, música y ruido.')))));
}

function uploaderReplace(root, project, caps) {
  clear(root);
  root.append(notice('warn', 'Al reemplazar el vídeo se perderán el análisis, los procesamientos y el montaje actuales.'));
  uploader(root, project, caps);
}
