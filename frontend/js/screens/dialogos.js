// Paso 4: diálogos por texto (voz sintética), micrófono o archivo de audio.
import { api, upload } from '../api.js';
import { h, field, select, toast, notice, fmtDuration, fmtTime, clear, debounce, confirmDialog, modal, fmtMoney } from '../ui.js';
import { trackJob, audioPlayer } from '../components.js';

export async function render(root, project) {
  const [voices, identities] = await Promise.all([api('/api/tts/voices'), api('/api/identities')]);
  const voiceIds = identities.filter(i => ['verified', 'verified_self'].includes(i.consent_status) && i.scopes.includes('voz'));
  let tab = 'texto';
  const tabs = h('div', { class: 'tabs' });
  const panel = h('div', { class: 'card' });
  const list = h('div');
  const drawTabs = () => {
    tabs.replaceChildren(...[['texto', 'A · Texto a voz'], ['micro', 'B · Grabar con micrófono'], ['archivo', 'C · Importar audio']].map(([k, l]) =>
      h('button', { class: `btn small ${tab === k ? 'active' : ''}`, onclick: () => { tab = k; drawTabs(); drawPanel(); } }, l)));
  };
  const drawPanel = () => {
    clear(panel);
    if (tab === 'texto') ttsPanel(panel, project, voices, refreshList);
    else if (tab === 'micro') micPanel(panel, project, voiceIds, refreshList);
    else filePanel(panel, project, voiceIds, refreshList);
  };
  async function refreshList() {
    const ds = await api(`/api/projects/${project.id}/dialogues`);
    clear(list).append(h('h2', {}, `Diálogos del proyecto (${ds.length})`));
    if (!ds.length) list.append(notice('info', 'Cree un diálogo para poder sincronizar los labios.'));
    for (const d of ds) list.append(await dialogueCard(project, d, refreshList));
  }
  drawTabs(); drawPanel();
  root.append(h('div', { class: 'cols' }, h('div', {}, tabs, panel, pronunciationCard(project)), list));
  await refreshList();
  root.append(h('div', { class: 'row end' }, h('a', { class: 'btn primary', href: `#/proyecto/${project.id}/labios` }, 'Continuar con la sincronización labial →')));
}

function rangeInput(label, min, max, step, value, fmt, onchange) {
  const out = h('output', {}, fmt(value));
  const input = h('input', { type: 'range', min, max, step, value, oninput: () => { out.textContent = fmt(Number(input.value)); onchange?.(Number(input.value)); } });
  return { el: field(label, h('div', { class: 'range-row' }, input, out)), input };
}

function ttsPanel(panel, project, voices, onDone) {
  if (!voices.length) {
    panel.append(notice('warn', 'No hay voces sintéticas configuradas en el servidor (eSpeak NG, Piper o ElevenLabs). Use las modalidades B o C.'));
    return;
  }
  const text = h('textarea', { maxlength: 2000, placeholder: 'Escriba aquí el diálogo. Use [pausa 0.8] para insertar silencios.' });
  const voice = select(voices.map(v => [v.id, `${v.name}${v.paid ? ' · de pago' : ' · gratuita'}`]), voices[0].id, () => update());
  const speed = rangeInput('Velocidad', 0.5, 2, 0.05, 1, v => `${v.toFixed(2)}×`, () => update());
  const pitch = rangeInput('Tono', -6, 6, 0.5, 0, v => `${v > 0 ? '+' : ''}${v} st`);
  const gain = rangeInput('Volumen', -20, 12, 1, 0, v => `${v > 0 ? '+' : ''}${v} dB`);
  const target = h('input', { type: 'number', min: 0.5, step: 0.1, placeholder: 'Automática' });
  const estimate = h('div', { class: 'notice info' }, 'Escriba el texto para ver la duración estimada.');
  const voiceInfo = h('small', { class: 'muted' });
  const jobBox = h('div');
  const update = debounce(async () => {
    const v = voices.find(x => x.id === voice.value);
    voiceInfo.textContent = `${v.license}${v.paid ? ' · Coste por caracteres según la tarifa del proveedor.' : ''}`;
    if (!text.value.trim()) return;
    try {
      const e = await api('/api/tts/estimate', { method: 'POST', body: { text: text.value, speed: Number(speed.input.value) } });
      estimate.textContent = `Duración estimada: ${e.seconds.toFixed(1)} s (habla ${e.speech_seconds.toFixed(1)} s + pausas ${e.pause_seconds.toFixed(1)} s · ${e.syllables} sílabas).`;
      if (target.value) {
        const ratio = e.seconds / Number(target.value);
        if (ratio > 1.25 || ratio < 0.8) estimate.append(h('div', {}, `⚠ Para encajar en ${target.value} s habría que cambiar mucho el ritmo; ajuste el texto o la velocidad.`));
      }
    } catch { /* estimación no crítica */ }
  }, 400);
  text.addEventListener('input', update);
  target.addEventListener('input', update);
  update();
  const insertPause = h('button', { class: 'btn small ghost', type: 'button', onclick: () => {
    const p = text.selectionStart ?? text.value.length;
    text.value = `${text.value.slice(0, p)} [pausa 0.5] ${text.value.slice(p)}`;
    update();
  } }, '+ Pausa');
  panel.append(h('h2', {}, 'Texto a voz sintética'),
    field('Diálogo', text), h('div', { class: 'row', style: { marginTop: '-6px' } }, insertPause), estimate,
    field('Voz autorizada', voice), voiceInfo, speed.el, pitch.el, gain.el,
    field('Duración objetivo (s)', target, 'Opcional: ajusta el ritmo (±20-25 %) para encajar en la escena.'),
    h('div', { class: 'row end' }, h('button', { class: 'btn primary', onclick: async () => {
      try {
        const r = await api(`/api/projects/${project.id}/dialogues/tts`, { method: 'POST', body: {
          text: text.value, voice_id: voice.value, speed: Number(speed.input.value), pitch: Number(pitch.input.value),
          gain_db: Number(gain.input.value), target_duration: target.value ? Number(target.value) : null } });
        if (r.estimated_cost_usd) toast(`Coste estimado: ${fmtMoney(r.estimated_cost_usd)}`, 'warn');
        const j = await trackJob(jobBox, r.job, { title: 'Generando voz' });
        if (j.status === 'succeeded') { (j.result.warnings || []).forEach(w => toast(w, 'warn', 8000)); text.value = ''; onDone(); }
      } catch (e) { toast(e.message, 'error'); }
    } }, 'Generar voz')), jobBox);
}

function ownerFields(voiceIds) {
  const owner = select([['yo', 'Es mi propia voz'], ['sintetica', 'Voz sintética generada por otra herramienta con licencia'], ['tercero', 'Es la voz de otra persona']], 'yo', v => { idWrap.hidden = v !== 'tercero'; });
  const ident = select([['', 'Seleccione…'], ...voiceIds.map(i => [i.id, i.display_name])], '');
  const idWrap = field('Persona (con consentimiento de voz)', ident, voiceIds.length ? null : 'No hay personas con consentimiento de voz verificado.');
  idWrap.hidden = true;
  const text = h('textarea', { maxlength: 2000, placeholder: 'Texto pronunciado (opcional, para subtítulos)', style: { minHeight: '70px' } });
  return { el: h('div', {}, field('¿De quién es la voz?', owner), idWrap, field('Transcripción', text, 'Si la deja vacía y el servidor tiene reconocimiento de voz, se transcribirá automáticamente.')), owner, ident, text };
}

async function sendAudio(project, blobOrFile, filename, mode, f, jobBox, onDone) {
  const fd = new FormData();
  fd.append('file', blobOrFile, filename);
  fd.append('mode', mode);
  fd.append('text', f.text.value);
  fd.append('voice_owner', f.owner.value);
  fd.append('voice_identity_id', f.ident.value);
  try {
    const r = await upload(`/api/projects/${project.id}/dialogues/audio`, fd);
    const j = await trackJob(jobBox, r.job, { title: 'Procesando audio' });
    if (j.status === 'succeeded') onDone();
  } catch (e) { toast(e.message, 'error'); }
}

function micPanel(panel, project, voiceIds, onDone) {
  const f = ownerFields(voiceIds);
  const jobBox = h('div');
  const status = h('div', { class: 'row' });
  let rec = null; let chunks = []; let blob = null; let stream = null; let timer = null;
  const mime = ['audio/mp4', 'audio/webm;codecs=opus', 'audio/webm'].find(m => window.MediaRecorder?.isTypeSupported?.(m)) || '';
  const start = h('button', { class: 'btn primary', onclick: async () => {
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } });
    } catch { return toast('No se pudo acceder al micrófono. Revise los permisos del navegador.', 'error'); }
    chunks = [];
    rec = new MediaRecorder(stream, mime ? { mimeType: mime } : undefined);
    rec.ondataavailable = e => e.data.size && chunks.push(e.data);
    rec.onstop = async () => {
      stream.getTracks().forEach(t => t.stop());
      blob = new Blob(chunks, { type: rec.mimeType });
      const url = URL.createObjectURL(blob);
      clear(status).append(h('audio', { controls: true, src: url }), send);
    };
    rec.start();
    const t0 = Date.now();
    clear(status).append(h('span', { class: 'rec-dot' }), h('span', { class: 'secs' }, '0 s'), stop);
    timer = setInterval(() => { status.querySelector('.secs').textContent = `${Math.round((Date.now() - t0) / 1000)} s`; }, 500);
  } }, '● Grabar');
  const stop = h('button', { class: 'btn danger', onclick: () => { clearInterval(timer); rec?.stop(); } }, '■ Detener');
  const send = h('button', { class: 'btn primary', onclick: () => {
    const ext = (blob.type.includes('mp4') ? 'm4a' : 'webm');
    sendAudio(project, blob, `grabacion.${ext}`, 'mic', f, jobBox, onDone);
  } }, 'Usar esta grabación');
  panel.append(h('h2', {}, 'Grabar con el micrófono'),
    !window.MediaRecorder ? notice('warn', 'Este navegador no permite grabar audio. Use la modalidad C.') : null,
    f.el, h('div', { class: 'row' }, start), status, jobBox);
}

function filePanel(panel, project, voiceIds, onDone) {
  const f = ownerFields(voiceIds);
  const jobBox = h('div');
  const input = h('input', { type: 'file', accept: 'audio/*,.m4a,.mp3,.wav,.caf' });
  panel.append(h('h2', {}, 'Importar un archivo de audio'), field('Archivo', input), f.el,
    h('div', { class: 'row end' }, h('button', { class: 'btn primary', onclick: () => {
      if (!input.files[0]) return toast('Seleccione un archivo.', 'warn');
      sendAudio(project, input.files[0], input.files[0].name, 'file', f, jobBox, onDone);
    } }, 'Importar')), jobBox);
}

async function dialogueCard(project, d, refresh) {
  const mode = { tts: 'Texto a voz', mic: 'Micrófono', file: 'Archivo' }[d.mode];
  const player = d.audio_asset_id ? await audioPlayer(d.audio_asset_id) : h('small', { class: 'muted' }, 'Generando audio…');
  return h('div', { class: 'card flat' },
    h('div', { class: 'row', style: { marginTop: 0 } }, h('strong', {}, mode), d.settings.voice_name ? h('span', { class: 'pill' }, d.settings.voice_name) : null,
      h('span', { class: 'pill' }, d.duration ? fmtDuration(d.duration) : `≈ ${fmtDuration(d.estimated_duration)}`)),
    d.text ? h('p', {}, d.text) : null, player,
    h('div', { class: 'row' },
      h('button', { class: 'btn small', disabled: !d.subtitles.length, onclick: () => editSubs(project, d, refresh) }, `Subtítulos (${d.subtitles.length})`),
      h('button', { class: 'btn small danger', onclick: async () => {
        if (!await confirmDialog('Eliminar diálogo', 'Se borrará el audio generado.', 'Eliminar', 'danger')) return;
        try { await api(`/api/projects/${project.id}/dialogues/${d.id}`, { method: 'DELETE' }); refresh(); } catch (e) { toast(e.message, 'error'); }
      } }, 'Eliminar')));
}

async function editSubs(project, d, refresh) {
  const rows = d.subtitles.map(s => ({ ...s }));
  const body = h('div', { class: 'list' }, rows.map(s => h('div', { class: 'list-item' },
    h('small', { class: 'muted' }, `${fmtTime(s.start)}–${fmtTime(s.end)}`),
    h('input', { type: 'text', value: s.text, class: 'grow', oninput: (e) => { s.text = e.target.value; } }))));
  const ok = await modal({ title: 'Editar subtítulos', wide: true, body, actions: [{ label: 'Cancelar', value: false }, { label: 'Guardar', value: true, kind: 'primary' }] });
  if (!ok) return;
  await api(`/api/projects/${project.id}/dialogues/${d.id}/subtitles`, { method: 'PUT', body: { subtitles: rows } });
  toast('Subtítulos guardados. Se aplicarán al sincronizar este diálogo.', 'ok');
  refresh();
}

function pronunciationCard(project) {
  const rules = { ...(project.settings.pronunciations || {}) };
  const list = h('div', { class: 'list' });
  const draw = () => clear(list).append(...Object.entries(rules).map(([k, v]) => h('div', { class: 'list-item' },
    h('span', { class: 'grow' }, `${k} → ${v}`), h('button', { class: 'btn small ghost', onclick: () => { delete rules[k]; save(); } }, 'Quitar'))));
  const save = async () => {
    await api(`/api/projects/${project.id}`, { method: 'PATCH', body: { pronunciations: rules } });
    draw();
  };
  const word = h('input', { type: 'text', placeholder: 'Palabra escrita' });
  const said = h('input', { type: 'text', placeholder: 'Cómo debe sonar' });
  draw();
  return h('details', { class: 'card' }, h('summary', {}, h('strong', {}, 'Pronunciación personalizada')),
    h('p', { class: 'muted' }, 'Sustituciones aplicadas sólo a la voz (no a los subtítulos). Ej.: «Melilla» → «Meliya».'),
    list, h('div', { class: 'row' }, word, said, h('button', { class: 'btn small', onclick: () => { if (word.value.trim()) { rules[word.value.trim()] = said.value.trim(); word.value = ''; said.value = ''; save(); } } }, 'Añadir')));
}
