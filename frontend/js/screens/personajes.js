// Paso 1: reparto. Personajes basados en personas autorizadas (su cara o su caricatura) o ficticios,
// con el estilo visual elegido y la voz sintética de cada uno.
import { api, capabilities } from '../api.js';
import { h, field, select, toast, notice, clear, confirmDialog, spinner } from '../ui.js';
import { trackJob, generate, styleSelect, providerNote, img } from '../components.js';

const STATUS = { pending: ['Sin retrato', 'warn'], generating: ['Generando…', 'info'], ready: ['Listo', 'ok'],
  failed: ['Error', 'err'], revoked: ['Consentimiento retirado', 'err'] };

const usable = (i) => ['verified', 'verified_self'].includes(i.consent_status) && i.scopes.includes('imagen') && i.photos.length;

export async function render(root, project) {
  const [caps, identities, voices] = await Promise.all([capabilities(), api('/api/identities'), api('/api/tts/voices')]);
  const styles = caps.styles;
  const styleLabel = (k) => styles.find(s => s.key === k)?.label || k;
  const list = h('div', { class: 'grid cast-grid' }, spinner());

  const projStyle = styleSelect(styles, project.style, async (v) => {
    try {
      await api(`/api/projects/${project.id}`, { method: 'PATCH', body: { style: v } });
      project.style = v;
      toast(`Estilo del vídeo: ${styleLabel(v)}. Los personajes nuevos lo usarán por defecto.`, 'ok');
    } catch (e) { toast(e.message, 'error'); }
  });

  async function refresh() {
    const cast = await api(`/api/projects/${project.id}/cast`);
    clear(list);
    if (!cast.length) list.append(notice('info', 'Añada al menos un personaje. Puede ser usted, una persona que lo haya autorizado o un personaje inventado.'));
    for (const c of cast) list.append(await castCard(c));
  }

  async function castCard(c) {
    const [label, kind] = STATUS[c.status] || [c.status, 'info'];
    const pic = h('div', { class: 'portrait' }, c.portrait_preview_asset_id ? await img(c.portrait_preview_asset_id, `Retrato de ${c.name}`) : h('span', { class: 'ph' }, c.identity_id ? '🙂' : '✨'));
    const jobBox = h('div');
    const ident = identities.find(i => i.id === c.identity_id);
    const gen = h('button', { class: 'btn small primary', disabled: c.status === 'revoked', onclick: async () => {
      gen.disabled = true;
      try {
        const r = await generate(`/api/projects/${project.id}/cast/${c.id}/portrait`);
        if (r) {
          const j = await trackJob(jobBox, r.job, { title: `Retrato de ${c.name}`, estimatedSeconds: 3 });
          if (j.status === 'succeeded') await refresh();
        }
      } catch (e) { toast(e.message, 'error'); } finally { gen.disabled = false; }
    } }, c.portrait_preview_asset_id ? 'Regenerar retrato' : 'Generar retrato');
    const desc = h('textarea', { maxlength: 500, rows: 2, placeholder: 'Rasgos, ropa, edad aparente… (opcional)' }, c.description || '');
    const st = styleSelect(styles, c.style);
    const voice = select([['', 'Voz por defecto'], ...voices.map(v => [v.id, `${v.name} (${v.language})`])], c.voice_id || '');
    const save = h('button', { class: 'btn small', onclick: async () => {
      try {
        await api(`/api/projects/${project.id}/cast/${c.id}`, { method: 'PATCH', body: { description: desc.value, style: st.value, voice_id: voice.value } });
        toast(st.value !== c.style ? 'Guardado. Regenere el retrato para aplicar el nuevo estilo.' : 'Guardado', 'ok');
        await refresh();
      } catch (e) { toast(e.message, 'error'); }
    } }, 'Guardar');
    const del = h('button', { class: 'btn small danger', onclick: async () => {
      if (!await confirmDialog('Quitar personaje', `Se eliminará «${c.name}» y su retrato. Las escenas en las que aparece dejarán de incluirlo.`, 'Quitar', 'danger')) return;
      await api(`/api/projects/${project.id}/cast/${c.id}`, { method: 'DELETE' });
      refresh();
    } }, 'Quitar');
    return h('div', { class: 'card cast-card' }, pic,
      h('div', { class: 'row', style: { marginTop: '8px' } }, h('strong', {}, c.name), h('span', { class: `pill ${kind}` }, label)),
      h('small', { class: 'muted' }, c.identity_id ? `Persona real: ${ident?.display_name || 'autorizada'}` : 'Personaje ficticio'),
      c.error ? notice('error', c.error) : null,
      field('Descripción', desc), h('div', { class: 'cols even' }, field('Estilo', st), field('Voz', voice)),
      h('div', { class: 'row' }, gen, save, del), jobBox);
  }

  // ---------------------------------------------- alta de personaje
  const ok = identities.filter(usable);
  let mode = ok.length ? 'real' : 'ficticio';
  const name = h('input', { type: 'text', maxlength: 40, placeholder: 'Nombre en el guion (p. ej. Ana)' });
  const who = select(ok.map(i => [i.id, `${i.display_name}${i.kind === 'self' ? ' (usted)' : ''}`]), ok[0]?.id, (v) => {
    if (!name.value) name.value = ok.find(i => i.id === v)?.display_name.split(' ')[0] || '';
  });
  if (ok[0] && !name.value) name.value = ok[0].display_name.split(' ')[0];
  const desc = h('textarea', { maxlength: 500, rows: 2, placeholder: mode === 'real' ? 'Ropa o detalles para el retrato (opcional)' : 'Ej.: un robot simpático de color naranja' });
  const st = styleSelect(styles, project.style);
  const voice = select([['', 'Voz por defecto'], ...voices.map(v => [v.id, `${v.name} (${v.language})`])], '');
  const realBox = h('div', {}, ok.length ? field('Persona autorizada', who, 'Sólo aparecen personas con consentimiento vigente para su imagen y al menos una foto.')
    : notice('warn', 'No tiene personas autorizadas con fotos. ', h('a', { href: '#/identidades' }, 'Añada la suya o pida consentimiento a otra persona'), '.'));
  const modeBtns = h('div', { class: 'row', style: { marginTop: 0 } });
  const drawMode = () => {
    clear(modeBtns).append(
      h('button', { type: 'button', class: `btn small ${mode === 'real' ? 'primary' : ''}`, onclick: () => { mode = 'real'; drawMode(); } }, 'Persona real (su cara o caricatura)'),
      h('button', { type: 'button', class: `btn small ${mode === 'ficticio' ? 'primary' : ''}`, onclick: () => { mode = 'ficticio'; drawMode(); } }, 'Personaje inventado'));
    realBox.hidden = mode !== 'real';
  };
  drawMode();
  const add = h('button', { class: 'btn primary', onclick: async () => {
    if (!name.value.trim()) return toast('Escriba el nombre del personaje.', 'warn');
    if (mode === 'real' && !ok.length) return toast('Necesita una persona autorizada.', 'warn');
    add.disabled = true;
    try {
      await api(`/api/projects/${project.id}/cast`, { method: 'POST', body: {
        name: name.value, identity_id: mode === 'real' ? who.value : null, description: desc.value, style: st.value, voice_id: voice.value || null } });
      name.value = ''; desc.value = '';
      await refresh();
    } catch (e) { toast(e.message, 'error'); } finally { add.disabled = false; }
  } }, 'Añadir personaje');

  root.append(providerNote(),
    h('div', { class: 'cols' },
      h('div', { class: 'card' }, h('h2', {}, 'Estilo del vídeo'), field('Estilo visual', projStyle,
        'Se aplica a los fotogramas de todas las escenas. Cada personaje puede tener su propio estilo de retrato.'),
      h('h2', {}, 'Nuevo personaje'), modeBtns, realBox, field('Nombre', name), field('Descripción', desc),
      h('div', { class: 'cols even' }, field('Estilo del retrato', st), field('Voz', voice)), add,
      notice('info', 'Los rasgos de una persona real sólo se usan con su consentimiento, que puede retirar en cualquier momento: al retirarlo se borran sus retratos, escenas y exportaciones. No se admiten personas famosas sin autorización ni menores.')),
      h('div', {}, h('h2', {}, 'Reparto'), list)),
    h('div', { class: 'row end' }, h('a', { class: 'btn primary', href: `#/proyecto/${project.id}/guion` }, 'Escribir el guion →')));
  await refresh();
}
