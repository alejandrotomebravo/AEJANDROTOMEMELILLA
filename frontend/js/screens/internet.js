// Paso 1: «Internet y vídeos». Búsqueda, análisis de URL e importación autorizada.
import { api, capabilities } from '../api.js';
import { h, field, toast, notice, clear, fmtDuration, fmtBytes, fmtDate, spinner, checkbox } from '../ui.js';
import { trackJob, licenseBox, provenanceForm } from '../components.js';
import { parseTime } from '../history.js';

export async function render(root, project) {
  const caps = await capabilities();
  const conns = caps.connectors;
  const searchable = conns.filter(c => c.searchable);
  let active = searchable.find(c => c.available && c.search !== false) || searchable[0];
  const detail = h('div');
  const results = h('div', { class: 'results' });
  const historyBox = h('div');

  // --------------------------------------------------- URL
  const urlIn = h('input', { type: 'url', placeholder: 'Pegue la URL de un vídeo (Wikimedia, Internet Archive, Pexels, Vimeo, YouTube o enlace directo a un archivo)', inputmode: 'url' });
  const analyze = h('button', { class: 'btn primary', onclick: async () => {
    if (!urlIn.value.trim()) return toast('Pegue una URL.', 'warn');
    analyze.disabled = true;
    clear(detail).append(spinner('Consultando la fuente…'));
    try {
      const c = await api('/api/internet/resolve', { method: 'POST', body: { url: urlIn.value.trim() } });
      showDetail(c, urlIn.value.trim());
    } catch (e) { clear(detail).append(notice('error', e.message)); } finally { analyze.disabled = false; }
  } }, 'Analizar URL');

  // --------------------------------------------------- búsqueda
  const tabs = h('div', { class: 'tabs' });
  const drawTabs = () => tabs.replaceChildren(...searchable.map(c => h('button', {
    class: `btn small ${c.name === active?.name ? 'active' : ''}`, onclick: () => { active = c; drawTabs(); },
  }, c.label, !c.available || c.search === false ? ' (no configurada)' : '')));
  drawTabs();
  const q = h('input', { type: 'search', placeholder: 'Palabras clave, p. ej. «faro», «mar», «Melilla»', enterkeyhint: 'search' });
  let openOnly = true;
  const openBox = checkbox('Sólo contenido con licencia abierta o dominio público', true, v => { openOnly = v; });
  const doSearch = async () => {
    if (!q.value.trim()) return;
    clear(results).append(spinner('Buscando…'));
    try {
      const r = await api(`/api/internet/search?connector=${active.name}&q=${encodeURIComponent(q.value.trim())}&open_only=${openOnly}`);
      clear(results);
      if (!r.results.length) results.append(notice('info', 'Sin resultados. Pruebe otras palabras u otra fuente.'));
      results.append(h('div', { class: 'grid' }, r.results.map(c => resultCard(c))));
    } catch (e) { clear(results).append(notice('error', e.message)); }
  };
  q.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); doSearch(); } });

  function resultCard(c) {
    return h('button', { class: 'card result', onclick: () => { showDetail(c, c.page_url); detail.scrollIntoView({ behavior: 'smooth' }); } },
      c.thumbnail_url ? h('img', { src: c.thumbnail_url, alt: '', loading: 'lazy', referrerpolicy: 'no-referrer' }) : h('div', { class: 'thumb-ph' }, '🎬'),
      h('strong', {}, c.title), h('small', { class: 'muted' }, [c.platform, c.author].filter(Boolean).join(' · ')),
      h('div', { class: 'row', style: { marginTop: '4px' } },
        h('span', { class: `pill ${c.importable ? 'ok' : 'warn'}` }, c.importable ? 'Importable' : 'Sólo consulta'),
        c.license?.name ? h('span', { class: 'pill' }, c.license.name) : null));
  }

  function showDetail(c, url) {
    clear(detail);
    const facts = h('dl', { class: 'kv' },
      h('dt', {}, 'Plataforma'), h('dd', {}, c.platform || '—'),
      h('dt', {}, 'Autor'), h('dd', {}, c.author_url ? h('a', { href: c.author_url, target: '_blank', rel: 'noopener noreferrer' }, c.author || c.author_url) : (c.author || 'No indicado')),
      h('dt', {}, 'Duración'), h('dd', {}, c.duration ? fmtDuration(c.duration) : 'Se conocerá al importar'),
      h('dt', {}, 'Resolución'), h('dd', {}, c.width ? `${c.width}×${c.height}` : '—'),
      h('dt', {}, 'Formato'), h('dd', {}, [c.format, c.size_bytes ? fmtBytes(c.size_bytes) : null].filter(Boolean).join(' · ') || '—'),
      h('dt', {}, 'Método'), h('dd', {}, c.method === 'A' ? 'A · descarga directa autorizada' : 'B · API oficial de la plataforma'),
      h('dt', {}, 'Página'), h('dd', {}, h('a', { href: c.page_url, target: '_blank', rel: 'noopener noreferrer' }, 'Abrir en la fuente')));
    const preview = c.embed_url
      ? h('iframe', { src: c.embed_url, class: 'embed', allow: 'fullscreen; picture-in-picture', referrerpolicy: 'strict-origin', title: c.title, loading: 'lazy' })
      : (c.thumbnail_url ? h('img', { src: c.thumbnail_url, class: 'embed', alt: '', referrerpolicy: 'no-referrer' }) : null);
    const box = h('div', { class: 'card' }, h('h2', {}, c.title), preview,
      preview ? h('small', { class: 'muted' }, 'La vista previa se carga desde la plataforma de origen, que recibe su dirección IP.') : null,
      facts, licenseBox(c.license));
    if (!c.importable) {
      box.append(notice('warn', h('strong', {}, 'No se puede importar: '), c.reason || 'la fuente no permite descargar este vídeo.'),
        c.alternatives?.length ? h('div', {}, h('strong', {}, 'Alternativas compatibles:'), h('ul', {}, c.alternatives.map(a => h('li', {}, a)))) : null,
        h('div', { class: 'row' }, h('a', { class: 'btn', href: `#/proyecto/${project.id}/archivos` }, 'Importar un archivo propio')));
      detail.append(box);
      return;
    }
    const start = h('input', { type: 'text', inputmode: 'decimal', placeholder: '0:00' });
    const end = h('input', { type: 'text', inputmode: 'decimal', placeholder: c.duration ? fmtMin(c.duration) : 'final' });
    const prov = provenanceForm(caps, { origin: c, compact: true });
    const jobBox = h('div');
    const go = h('button', { class: 'btn primary', onclick: async () => {
      const s = start.value ? parseTime(start.value) : null;
      const e = end.value ? parseTime(end.value) : null;
      if ((s !== null && Number.isNaN(s)) || (e !== null && Number.isNaN(e))) return toast('Tiempo no válido: use minutos:segundos.', 'warn');
      go.disabled = true;
      try {
        const r = await api(`/api/projects/${project.id}/sources/internet`, { method: 'POST', body: { url, start: s, end: e, provenance: prov.value() } });
        const j = await trackJob(jobBox, r.job, { title: `Importando desde ${c.platform}` });
        if (j.status === 'succeeded') {
          const a = await api(`/api/jobs/${j.result.analysis_job_id}`);
          await trackJob(jobBox, a, { title: 'Analizando escenas, rostros y audio' });
          toast('Vídeo importado y añadido a la línea de tiempo', 'ok');
          jobBox.append(h('div', { class: 'row' }, h('a', { class: 'btn primary', href: `#/proyecto/${project.id}/escenas` }, 'Seleccionar escenas →')));
          loadHistory();
        }
      } catch (err) {
        toast(err.message, 'error');
      } finally { go.disabled = false; }
    } }, 'Importar vídeo');
    box.append(
      h('h3', { style: { marginTop: '14px' } }, 'Seleccionar fragmento (opcional)'),
      h('div', { class: 'cols even' }, field('Inicio (min:seg)', start), field('Final (min:seg)', end)),
      h('small', { class: 'muted' }, 'Se descarga el archivo y sólo se conserva el fragmento elegido.'),
      h('h3', { style: { marginTop: '14px' } }, 'Derechos sobre el material'), prov.el,
      h('div', { class: 'row end' }, go), jobBox);
    detail.append(box);
  }

  async function loadHistory() {
    const list = await api('/api/internet/history');
    clear(historyBox).append(h('div', { class: 'card' }, h('h2', {}, 'Historial de importaciones'),
      list.length ? h('div', { class: 'list' }, list.slice(0, 20).map(i => h('div', { class: 'list-item' },
        h('div', { class: 'grow' }, h('strong', {}, i.label), h('div', {}, h('small', { class: 'muted' },
          `${i.platform || ''} · ${i.license || 'licencia no indicada'} · ${i.project} · ${fmtDate(i.created_at)}`))),
        h('span', { class: `pill ${i.status === 'ready' ? 'ok' : i.status === 'failed' ? 'err' : 'warn'}` },
          { ready: 'Importado', failed: 'Error', pending: 'Pendiente', importing: 'Importando' }[i.status] || i.status),
        i.page_url ? h('a', { href: i.page_url, target: '_blank', rel: 'noopener noreferrer', class: 'btn small ghost' }, 'Origen') : null)))
        : h('p', { class: 'muted' }, 'Aún no ha importado vídeos de Internet.')));
  }

  root.append(
    notice('info', 'Sólo se importan vídeos cuya fuente permite la descarga (licencias abiertas, dominio público, API oficiales o enlaces directos autorizados). ',
      'No se eluden DRM, inicios de sesión ni restricciones de las plataformas, y no se piden contraseñas.'),
    h('div', { class: 'cols' },
      h('div', {},
        h('div', { class: 'card' }, h('h2', {}, 'Importar desde una URL'), h('div', { class: 'url-row' }, urlIn, analyze)),
        h('div', { class: 'card' }, h('h2', {}, 'Buscar vídeos'), tabs,
          active ? h('small', { class: 'muted' }, active.description) : null,
          h('div', { class: 'url-row', style: { marginTop: '10px' } }, q, h('button', { class: 'btn', onclick: doSearch }, 'Buscar')), openBox.el),
        results),
      h('div', {}, detail, sourcesInfo(conns), historyBox)));
  loadHistory();
}

function fmtMin(s) { const m = Math.floor(s / 60); return `${m}:${String(Math.floor(s % 60)).padStart(2, '0')}`; }

function sourcesInfo(conns) {
  return h('details', { class: 'card' }, h('summary', {}, h('strong', {}, 'Fuentes disponibles y condiciones')),
    h('div', { class: 'list', style: { marginTop: '10px' } }, conns.map(c => h('div', { class: 'list-item' },
      h('div', { class: 'grow' }, h('strong', {}, c.label), h('div', {}, h('small', { class: 'muted' }, c.description)),
        c.reason ? h('div', {}, h('small', { class: 'muted' }, c.reason)) : null),
      h('span', { class: `pill ${c.available ? (c.importable === false ? 'warn' : 'ok') : 'err'}` },
        !c.available ? 'No configurada' : c.importable === false ? 'Sólo consulta' : 'Disponible'),
      c.terms_url ? h('a', { href: c.terms_url, target: '_blank', rel: 'noopener noreferrer', class: 'btn small ghost' }, 'Condiciones') : null))));
}
