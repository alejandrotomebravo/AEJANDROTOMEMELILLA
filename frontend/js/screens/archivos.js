// Paso 2: importación de archivos del móvil u ordenador (por lotes) y gestión de fuentes.
import { api, upload, capabilities } from '../api.js';
import { h, toast, notice, clear, fmtDuration, fmtBytes, fmtDate, confirmDialog, spinner } from '../ui.js';
import { trackJob, provenanceForm, img, licenseBox } from '../components.js';

export async function render(root, project) {
  const caps = await capabilities();
  if (!project.use_declaration) {
    root.append(notice('warn', 'Falta la declaración de uso responsable de este proyecto. ', h('a', { href: '#/nuevo' }, 'Cree el proyecto desde el asistente.')));
    return;
  }
  const listBox = h('div');
  let chosen = [];
  const fileList = h('div', { class: 'list' });
  const input = h('input', { type: 'file', accept: 'video/*,.mov,.mp4,.m4v,.webm,.mkv', multiple: true, hidden: true,
    onchange: () => { chosen = [...input.files]; drawChosen(); } });
  const drop = h('div', { class: 'card new-card', style: { minHeight: '170px', cursor: 'pointer' }, role: 'button', tabindex: 0,
    onclick: () => input.click(), onkeydown: (e) => e.key === 'Enter' && input.click(),
    ondragover: (e) => e.preventDefault(), ondrop: (e) => { e.preventDefault(); chosen = [...e.dataTransfer.files]; drawChosen(); } },
  h('div', { style: { textAlign: 'center' } }, h('div', { style: { fontSize: '2.2rem' } }, '⬆'),
    'Elegir vídeos de la galería, Archivos (iCloud Drive, Google Drive…) o del ordenador',
    h('div', {}, h('small', { class: 'muted' }, `Uno o varios · MP4, MOV, WEBM… · máx. ${caps.limits.max_upload_mb} MB y ${fmtDuration(caps.limits.max_video_seconds)} por vídeo`))));
  const prov = provenanceForm(caps);
  const jobs = h('div');
  const send = h('button', { class: 'btn primary', onclick: async () => {
    if (!chosen.length) return toast('Elija al menos un vídeo.', 'warn');
    const big = chosen.find(f => f.size > caps.limits.max_upload_mb * 1024 * 1024);
    if (big) return toast(`«${big.name}» supera el tamaño máximo.`, 'error');
    const fd = new FormData();
    chosen.forEach(f => fd.append('files', f, f.name));
    fd.append('provenance', JSON.stringify(prov.value()));
    const bar = h('div', { class: 'bar-fill' });
    const label = h('span', {}, '0 %');
    clear(jobs).append(h('div', { class: 'job' }, h('div', { class: 'job-head' }, h('strong', {}, `Subiendo ${chosen.length} archivo(s)`), label), h('div', { class: 'bar' }, bar)));
    send.disabled = true;
    try {
      const r = await upload(`/api/projects/${project.id}/sources/upload`, fd, p => { bar.style.width = `${p * 100}%`; label.textContent = `${Math.round(p * 100)} %`; });
      clear(jobs);
      for (const c of r.created) {
        const box = h('div');
        jobs.append(box);
        const j = await trackJob(box, c.job, { title: `Importando ${c.label}` });
        if (j.status === 'succeeded') {
          const a = await api(`/api/jobs/${j.result.analysis_job_id}`);
          await trackJob(box, a, { title: `Analizando ${c.label}` });
        }
      }
      chosen = []; drawChosen();
      toast('Importación terminada', 'ok');
      refresh();
    } catch (e) { toast(e.message, 'error'); clear(jobs); } finally { send.disabled = false; }
  } }, 'Importar');
  function drawChosen() {
    clear(fileList).append(...chosen.map(f => h('div', { class: 'list-item' }, h('span', { class: 'grow' }, f.name), h('small', { class: 'muted' }, fmtBytes(f.size)))));
  }

  async function refresh() {
    clear(listBox).append(spinner());
    const sources = await api(`/api/projects/${project.id}/sources`);
    clear(listBox).append(h('h2', {}, `Vídeos del proyecto (${sources.length})`));
    if (!sources.length) listBox.append(notice('info', 'Aún no hay vídeos. Importe archivos aquí o desde «Internet».'));
    for (const s of sources) listBox.append(await sourceCard(project, s, refresh));
  }

  root.append(h('div', { class: 'cols' },
    h('div', {}, drop, input, fileList,
      h('div', { class: 'card' }, h('h2', {}, 'Derechos sobre los vídeos que importa'), prov.el, h('div', { class: 'row end' }, send), jobs),
      notice('info', 'Sólo se accede a los archivos que usted elija. Consejo: los mejores resultados se obtienen con rostros de frente, bien iluminados y de al menos 128 píxeles de alto.')),
    listBox));
  refresh();
}

export async function sourceCard(project, s, refresh) {
  const st = { ready: ['Listo', 'ok'], importing: ['Importando…', 'warn'], pending: ['En cola', 'warn'], failed: ['Error', 'err'] }[s.status] || [s.status, ''];
  const thumb = s.sprite_asset_id ? await img(s.sprite_asset_id, '') : h('div', { class: 'thumb-ph' }, '🎬');
  thumb.classList?.add('sprite-thumb');
  const o = s.origin || {};
  return h('div', { class: 'card flat source-card' },
    h('div', { class: 'row', style: { marginTop: 0 } }, h('strong', { class: 'grow' }, s.label), h('span', { class: `pill ${st[1]}` }, st[0]),
      s.processed ? h('span', { class: 'pill' }, 'Procesado con IA') : null),
    s.status === 'ready' ? h('div', { class: 'sprite-wrap' }, thumb) : null,
    s.error ? notice('error', s.error) : null,
    h('dl', { class: 'kv' },
      h('dt', {}, 'Origen'), h('dd', {}, o.page_url ? h('a', { href: o.page_url, target: '_blank', rel: 'noopener noreferrer' }, o.platform || 'Internet') : (o.platform || 'Archivo local')),
      o.author ? [h('dt', {}, 'Autor'), h('dd', {}, o.author)] : null,
      h('dt', {}, 'Importado'), h('dd', {}, o.imported_at ? fmtDate(o.imported_at) : fmtDate(s.created_at)),
      s.duration ? [h('dt', {}, 'Duración'), h('dd', {}, `${fmtDuration(s.duration)} · ${s.width}×${s.height}`)] : null,
      s.fragment ? [h('dt', {}, 'Fragmento'), h('dd', {}, `${s.fragment.start ?? 0} s – ${s.fragment.end ?? 'final'}`)] : null,
      h('dt', {}, 'Derechos'), h('dd', {}, s.provenance ? `${s.provenance.source_type}${s.provenance.can_distribute ? '' : ' · sin permiso de difusión'}` : 'Sin declarar')),
    o.license ? licenseBox(o.license) : null,
    (s.provenance?.warnings || []).length ? notice('warn', h('ul', {}, s.provenance.warnings.map(w => h('li', {}, w)))) : null,
    h('div', { class: 'row' },
      h('button', { class: 'btn small', onclick: async () => {
        const name = prompt('Nuevo nombre', s.label);
        if (name) { await api(`/api/projects/${project.id}/sources/${s.id}`, { method: 'PATCH', body: { label: name } }); refresh(); }
      } }, 'Renombrar'),
      h('button', { class: 'btn small danger', onclick: async () => {
        if (!await confirmDialog('Eliminar vídeo', `Se borrará «${s.label}», sus versiones procesadas y sus escenas de la línea de tiempo.`, 'Eliminar', 'danger')) return;
        await api(`/api/projects/${project.id}/sources/${s.id}`, { method: 'DELETE' });
        toast('Vídeo eliminado', 'ok'); refresh();
      } }, 'Eliminar')));
}
