// Paso 8: marca de agua, opciones de salida, estimación y exportación.
import { api, capabilities, assetUrl } from '../api.js';
import { h, field, select, toast, notice, clear, checkbox, fmtDuration, fmtBytes, fmtDate, modal } from '../ui.js';
import { trackJob } from '../components.js';

const POS = { 'top-left': 'Arriba izquierda', 'top-center': 'Arriba centro', 'top-right': 'Arriba derecha',
  'bottom-left': 'Abajo izquierda', 'bottom-center': 'Abajo centro', 'bottom-right': 'Abajo derecha' };

export async function render(root, project) {
  if (!project.proxy_asset_id) { root.append(notice('warn', 'Importe primero un vídeo.')); return; }
  const caps = await capabilities();
  const wmc = caps.watermark;
  const wm = { ...project.watermark };
  const exp = project.settings.export || { resolution: '1080p', fps: 30, codec: 'h264' };

  // ----------------------------------------------- marca de agua
  const mock = h('div', { class: 'wm-preview', style: { aspectRatio: project.aspect.replace(':', '/') , maxHeight: '320px', margin: '0 auto' } });
  const drawMock = () => {
    const lines = [wmc.base_text, [...wm.labels, wm.custom].filter(Boolean).join(' · ')].filter(Boolean);
    const box = h('div', { class: 'wm', style: { opacity: wm.opacity, fontSize: `${Math.max(8, wm.size * 320)}px` } }, ...lines.map((l, i) => h('div', { style: { fontSize: i ? '0.8em' : '1em' } }, l)));
    const [v, hz] = wm.position.split('-');
    Object.assign(box.style, { [v === 'top' ? 'top' : 'bottom']: '3%' });
    if (hz === 'left') box.style.left = '3%'; else if (hz === 'right') box.style.right = '3%'; else { box.style.left = '50%'; box.style.transform = 'translateX(-50%)'; }
    clear(mock).append(box);
  };
  const saveWm = async () => {
    try {
      const p = await api(`/api/projects/${project.id}`, { method: 'PATCH', body: { watermark: wm } });
      Object.assign(wm, p.watermark);
      drawMock();
    } catch (e) { toast(e.message, 'error'); }
  };
  const pos = select(Object.entries(POS), wm.position, v => { wm.position = v; saveWm(); });
  const size = h('input', { type: 'range', min: wmc.size[0], max: wmc.size[1], step: 0.002, value: wm.size, oninput: () => { wm.size = Number(size.value); drawMock(); }, onchange: saveWm });
  const opacity = h('input', { type: 'range', min: wmc.opacity[0], max: wmc.opacity[1], step: 0.05, value: wm.opacity, oninput: () => { wm.opacity = Number(opacity.value); drawMock(); }, onchange: saveWm });
  const labels = wmc.labels.map(l => checkbox(l, wm.labels.includes(l), v => { wm.labels = v ? [...wm.labels, l] : wm.labels.filter(x => x !== l); saveWm(); }).el);
  const custom = h('input', { type: 'text', maxlength: 80, value: wm.custom, placeholder: 'Aviso personalizado (opcional)', onchange: () => { wm.custom = custom.value; saveWm(); } });
  drawMock();

  // ----------------------------------------------- salida
  const res = select(caps.export.resolutions.map(r => [r, r === '2160p' ? '4K (2160p)' : r]), exp.resolution);
  const fps = select(caps.export.fps.map(f => [f, `${f} fps`]), exp.fps);
  const codec = select([['h264', 'MP4 · H.264'], ...(caps.export.h265 ? [['h265', 'MP4 · H.265/HEVC']] : [])], exp.codec);
  const estBox = h('div', { class: 'notice info' }, 'Calculando…');
  const srcFps = project.source?.fps || 30;
  const estimate = async () => {
    try {
      const e = await api(`/api/projects/${project.id}/estimate`, { method: 'POST', body: { resolution: res.value, fps: Number(fps.value), codec: codec.value } });
      clear(estBox).append(h('dl', { class: 'kv' },
        h('dt', {}, 'Salida'), h('dd', {}, `${e.width}×${e.height} · ${fps.value} fps · ${codec.value.toUpperCase()}`),
        h('dt', {}, 'Duración'), h('dd', {}, fmtDuration(e.duration_seconds)),
        h('dt', {}, 'Tamaño aprox.'), h('dd', {}, `${e.estimated_size_mb} MB`),
        h('dt', {}, 'Tiempo aprox.'), h('dd', {}, `${fmtDuration(e.estimated_render_seconds)} (local, sin coste)`)),
      Number(fps.value) > srcFps + 1 ? h('div', {}, h('small', {}, `⚠ El original tiene ${Math.round(srcFps)} fps: a ${fps.value} fps se duplicarán fotogramas, no se ganará fluidez.`)) : '',
      h('small', { class: 'muted' }, e.note));
      api(`/api/projects/${project.id}`, { method: 'PATCH', body: { export: { resolution: res.value, fps: Number(fps.value), codec: codec.value } } }).catch(() => {});
    } catch (e) { clear(estBox).append(e.message); }
  };
  [res, fps, codec].forEach(s => s.addEventListener('change', estimate));
  estimate();

  const jobBox = h('div');
  const exportsBox = h('div');
  const go = h('button', { class: 'btn primary', onclick: async () => {
    let ack = false;
    const c = checkbox('He leído el aviso y dispongo de las autorizaciones necesarias.', false, v => { ack = v; });
    const ok = await modal({ title: 'Aviso de contenido sintético', body: h('div', {},
      h('p', {}, 'El vídeo exportado llevará de forma permanente el aviso visible:'), h('p', {}, h('strong', {}, `«${wmc.base_text}»`)),
      notice('warn', wmc.disclaimer), c.el),
    actions: [{ label: 'Cancelar', value: false }, { label: 'Exportar', value: true, kind: 'primary', validate: () => ack || (toast('Debe aceptar el aviso.', 'warn'), false) }] });
    if (!ok) return;
    try {
      const r = await api(`/api/projects/${project.id}/export`, { method: 'POST', body: { resolution: res.value, fps: Number(fps.value), codec: codec.value, ack_disclaimer: true } });
      const j = await trackJob(jobBox, r.job, { title: 'Exportando', estimatedSeconds: r.estimate.estimated_render_seconds });
      if (j.status === 'succeeded') { toast('Exportación terminada', 'ok'); listExports(exportsBox, project); }
    } catch (e) { toast(e.message, 'error'); }
  } }, 'Exportar vídeo');

  root.append(h('div', { class: 'cols' },
    h('div', { class: 'card' }, h('h2', {}, 'Marca de agua obligatoria'), mock,
      notice('info', 'La marca acompaña a todas las salidas (también vistas previas y baja resolución) y no puede eliminarse. Puede ajustar su posición, tamaño y opacidad.'),
      field('Posición', pos), field('Tamaño', size), field('Opacidad', opacity),
      h('div', { class: 'field' }, h('span', { class: 'label' }, 'Etiquetas adicionales'), ...labels), field('Aviso personalizado', custom)),
    h('div', {}, h('div', { class: 'card' }, h('h2', {}, 'Formato de salida'), field('Resolución', res), field('Fotogramas por segundo', fps), field('Códec', codec), estBox,
      h('h3', {}, 'Metadatos de procedencia'),
      caps.c2pa.available
        ? notice(caps.c2pa.signed_with_own_certificate ? 'ok' : 'warn', caps.c2pa.signed_with_own_certificate ? 'Se incrustará un manifiesto C2PA firmado.' : 'Se incrustará un manifiesto C2PA con certificado de pruebas (los verificadores lo marcarán como no confiable).')
        : notice('info', 'C2PA no está instalado en el servidor: se añadirán etiquetas de metadatos MP4 y un archivo de procedencia JSON (no firmado).'),
      h('div', { class: 'row end' }, go), jobBox),
    exportsBox)));
  listExports(exportsBox, project);
}

async function listExports(box, project) {
  const list = await api(`/api/projects/${project.id}/exports`);
  clear(box).append(h('div', { class: 'card' }, h('h2', {}, `Exportaciones (${list.length})`),
    list.length ? h('div', { class: 'list' }, list.map(e => h('div', { class: 'list-item' },
      h('div', { class: 'grow' }, h('strong', {}, `${e.meta.resolution} · ${e.meta.fps} fps · ${String(e.meta.codec).toUpperCase()}`),
        h('div', {}, h('small', { class: 'muted' }, `${fmtDate(e.created_at)} · ${fmtBytes(e.size)} · C2PA: ${e.meta.c2pa?.c2pa ? 'sí' : 'no'}`))),
      h('button', { class: 'btn small primary', onclick: async () => { location.href = await assetUrl(e.id, true); } }, 'Vídeo'),
      e.meta.srt_asset_id ? h('button', { class: 'btn small', onclick: async () => { location.href = await assetUrl(e.meta.srt_asset_id, true); } }, 'Subtítulos .srt') : null,
      e.meta.manifest_asset_id ? h('button', { class: 'btn small', onclick: async () => { location.href = await assetUrl(e.meta.manifest_asset_id, true); } }, 'Procedencia .json') : null))) : h('p', { class: 'muted' }, 'Todavía no hay exportaciones.')));
}
