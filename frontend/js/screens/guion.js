// Paso 2: instrucciones → guion gráfico por reglas (gratuito), editable.
import { api, capabilities } from '../api.js';
import { h, field, toast, notice, clear, modal, checkbox, select, confirmDialog } from '../ui.js';
import { trackJob, generate } from '../components.js';

const uid = () => Math.random().toString(36).slice(2, 12);
const EXAMPLE = 'Ana llega a la playa de Melilla al amanecer con una tabla de surf. Luis, un robot naranja, la saluda: «¡Hoy es el gran día!». Los dos se ríen y corren hacia el agua. Final con un plano general del atardecer.';

export async function render(root, project) {
  const caps = await capabilities();
  const cast = project.cast;
  const names = cast.map(c => c.name);
  let board = structuredClone(project.storyboard);
  let dirty = false;

  // ---------------------------------------------- instrucciones
  const brief = h('textarea', { rows: 6, maxlength: 6000, placeholder: EXAMPLE }, project.brief || '');
  const maxScenes = h('input', { type: 'number', min: 1, max: 12, value: Math.max(1, board.scenes.length || 4) });
  const target = h('input', { type: 'number', min: 5, max: 120, step: 5, placeholder: 'Opcional' });
  const jobBox = h('div');
  const gen = h('button', { class: 'btn primary', onclick: async () => {
    if (!brief.value.trim()) return toast('Escriba las instrucciones del vídeo.', 'warn');
    if (board.scenes.length && !await confirmDialog('Reemplazar el guion', 'Se sustituirá el guion actual y se borrarán los fotogramas y clips ya generados.', 'Reemplazar', 'danger')) return;
    gen.disabled = true;
    try {
      await api(`/api/projects/${project.id}`, { method: 'PATCH', body: { brief: brief.value } });
      const r = await generate(`/api/projects/${project.id}/storyboard/generate`,
        { max_scenes: Number(maxScenes.value) || 4, target_seconds: Number(target.value) || null });
      const j = await trackJob(jobBox, r.job, { title: 'Preparando el guion', estimatedSeconds: 2 });
      if (j.status === 'succeeded') {
        const p = await api(`/api/projects/${project.id}`);
        Object.assign(project, p);
        board = structuredClone(p.storyboard); dirty = false; draw();
        toast(`Guion listo: ${board.scenes.length} escenas`, 'ok');
      }
    } catch (e) { toast(e.message, 'error'); } finally { gen.disabled = false; }
  } }, board.scenes.length ? 'Volver a generar el guion' : 'Generar guion y prompts');

  const llmNote = notice('info', 'Cómo escribir las instrucciones: cada frase será una escena; nombre a los personajes por su nombre y ponga lo que dicen entre comillas «…». Después puede retocar cada escena a mano.');

  // ---------------------------------------------- guion editable
  const boardBox = h('div');
  const saveBtn = h('button', { class: 'btn primary', onclick: () => save() }, 'Guardar cambios del guion');
  const markDirty = () => { dirty = true; saveBtn.textContent = 'Guardar cambios del guion •'; };

  async function save() {
    try {
      const r = await api(`/api/projects/${project.id}/storyboard`, { method: 'PUT', body: board });
      board = r; dirty = false; saveBtn.textContent = 'Guardar cambios del guion';
      toast('Guion guardado', 'ok'); draw();
      return true;
    } catch (e) { toast(e.message, 'error'); return false; }
  }

  function sceneCard(s, i) {
    const inp = (key, attrs = {}) => h('input', { type: 'text', value: s[key] ?? '', ...attrs, oninput: (e) => { s[key] = e.target.value; markDirty(); } });
    const area = (key, rows = 2) => h('textarea', { rows, oninput: (e) => { s[key] = e.target.value; markDirty(); } }, s[key] ?? '');
    const chars = h('div', { class: 'row', style: { marginTop: 0 } }, names.map(n => checkbox(n, s.characters.includes(n), v => {
      s.characters = v ? [...s.characters, n] : s.characters.filter(x => x !== n); markDirty();
    }).el));
    const dur = h('input', { type: 'number', min: 3, max: 10, step: 1, value: s.duration_seconds, oninput: (e) => { s.duration_seconds = Number(e.target.value); markDirty(); } });
    const dlgBox = h('div', { class: 'list' });
    const drawDlg = () => {
      clear(dlgBox).append(...s.dialogue.map((d, k) => h('div', { class: 'list-item' },
        select(names.map(n => [n, n]), d.character, v => { d.character = v; markDirty(); }),
        h('input', { type: 'text', class: 'grow', maxlength: 300, value: d.line, oninput: (e) => { d.line = e.target.value; markDirty(); } }),
        h('button', { class: 'btn small ghost', 'aria-label': 'Quitar frase', onclick: () => { s.dialogue.splice(k, 1); markDirty(); drawDlg(); } }, '✕'))),
      names.length && s.dialogue.length < 8 ? h('button', { class: 'btn small', onclick: () => { s.dialogue.push({ character: s.characters[0] || names[0], line: '' }); markDirty(); drawDlg(); } }, '+ Frase') : '');
    };
    drawDlg();
    const media = [s.keyframe ? 'fotograma' : null, s.clip ? 'clip' : null].filter(Boolean);
    return h('div', { class: 'card scene-card' },
      h('div', { class: 'row', style: { marginTop: 0 } }, h('span', { class: 'pill' }, `Escena ${i + 1}`),
        media.length ? h('span', { class: 'pill ok' }, `Con ${media.join(' y ')}`) : null, h('span', { class: 'spacer' }),
        h('button', { class: 'btn small ghost', disabled: i === 0, 'aria-label': 'Subir', onclick: () => { board.scenes.splice(i - 1, 0, board.scenes.splice(i, 1)[0]); markDirty(); draw(); } }, '↑'),
        h('button', { class: 'btn small ghost', disabled: i === board.scenes.length - 1, 'aria-label': 'Bajar', onclick: () => { board.scenes.splice(i + 1, 0, board.scenes.splice(i, 1)[0]); markDirty(); draw(); } }, '↓'),
        h('button', { class: 'btn small', onclick: () => rewrite(s) }, 'Reescribir'),
        h('button', { class: 'btn small danger', onclick: () => { board.scenes.splice(i, 1); markDirty(); draw(); } }, 'Eliminar')),
      field('Título', inp('title', { maxlength: 120 })), field('Qué ocurre', area('description', 3)),
      h('div', { class: 'cols even' }, field('Lugar', inp('setting')), field('Cámara', inp('camera', { placeholder: 'Plano medio, acercamiento lento…' }))),
      h('div', { class: 'cols even' }, h('div', { class: 'field' }, h('span', { class: 'label' }, 'Personajes en escena'), chars), field('Duración (s)', dur, null)),
      h('div', { class: 'field' }, h('span', { class: 'label' }, 'Diálogos'), dlgBox),
      h('details', {}, h('summary', {}, 'Prompts para los modelos de IA (en inglés)'),
        field('Fotograma clave (imagen)', area('keyframe_prompt', 3)), field('Movimiento (vídeo)', area('video_prompt', 3)),
        h('small', { class: 'muted' }, 'El estilo visual y las caras de los personajes se añaden automáticamente.')),
      media.length ? h('small', { class: 'muted' }, 'Si cambia esta escena, vuelva a generar su fotograma y su clip para aplicar los cambios.') : null);
  }

  async function rewrite(s) {
    const ins = h('textarea', { rows: 3, maxlength: 1000, placeholder: 'Ej.: que Luis tropiece de forma cómica y Ana le ayude' });
    const v = await modal({ title: `Reescribir «${s.title}»`, body: h('div', {}, field('¿Qué quiere cambiar?', ins)),
      actions: [{ label: 'Cancelar', value: false }, { label: 'Reescribir', value: true, kind: 'primary' }] });
    if (!v || !ins.value.trim()) return;
    if (dirty && !await save()) return;
    try {
      const r = await generate(`/api/projects/${project.id}/scenes/${s.id}/rewrite`, { instruction: ins.value });
      const j = await trackJob(jobBox, r.job, { title: 'Reescribiendo la escena' });
      if (j.status === 'succeeded') { board = structuredClone((await api(`/api/projects/${project.id}`)).storyboard); draw(); }
    } catch (e) { toast(e.message, 'error'); }
  }

  function draw() {
    const title = h('input', { type: 'text', maxlength: 120, value: board.title || '', oninput: (e) => { board.title = e.target.value; markDirty(); } });
    const logline = h('input', { type: 'text', maxlength: 300, value: board.logline || '', oninput: (e) => { board.logline = e.target.value; markDirty(); } });
    clear(boardBox);
    if (!board.scenes.length) {
      boardBox.append(notice('info', 'Todavía no hay guion. Escriba sus instrucciones y pulse «Generar guion y prompts», o añada escenas a mano.'));
    } else {
      const total = board.scenes.reduce((a, s) => a + (Number(s.duration_seconds) || 0), 0);
      boardBox.append(h('div', { class: 'card' }, h('div', { class: 'cols even' }, field('Título del vídeo', title), field('Resumen', logline)),
        h('small', { class: 'muted' }, `${board.scenes.length} escenas · ${total} s en total`)));
      board.scenes.forEach((s, i) => boardBox.append(sceneCard(s, i)));
    }
    boardBox.append(h('div', { class: 'row' },
      board.scenes.length < 12 ? h('button', { class: 'btn', onclick: () => {
        board.scenes.push({ id: uid(), title: `Escena ${board.scenes.length + 1}`, description: '', setting: '', characters: names.slice(0, 2), camera: 'Plano medio',
          duration_seconds: 5, dialogue: [], keyframe_prompt: '', video_prompt: '', keyframe: null, clip: null });
        markDirty(); draw();
      } }, '+ Añadir escena') : null,
      h('span', { class: 'spacer' }), board.scenes.length || dirty ? saveBtn : null));
  }
  draw();

  const next = h('a', { class: 'btn primary', href: `#/proyecto/${project.id}/escenas`, onclick: async (e) => {
    if (dirty) { e.preventDefault(); if (await save()) location.hash = `#/proyecto/${project.id}/escenas`; }
  } }, 'Generar las escenas →');
  root.append(
    !cast.length ? notice('warn', 'Aún no hay personajes. ', h('a', { href: `#/proyecto/${project.id}/personajes` }, 'Añádalos primero'), ' para que el guion los incluya.') : '',
    h('div', { class: 'card' }, h('h2', {}, 'Instrucciones del vídeo'),
      h('p', { class: 'muted' }, `Describa la historia con sus propias palabras. Personajes disponibles: ${names.join(', ') || 'ninguno'}. Estilo: ${caps.styles.find(s => s.key === project.style)?.label || project.style}.`),
      field('¿Qué debe pasar en el vídeo?', brief),
      h('div', { class: 'cols even' }, field('Número máximo de escenas', maxScenes), field('Duración total deseada (s)', target)),
      llmNote, gen, jobBox),
    h('h2', {}, 'Guion gráfico'), boardBox,
    h('div', { class: 'row end' }, next));
  return () => { if (dirty) toast('Hay cambios del guion sin guardar.', 'warn'); };
}
