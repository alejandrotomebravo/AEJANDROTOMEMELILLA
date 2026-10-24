// Componentes reutilizables: progreso de trabajos, reproductor y pasos del asistente.
import { api, assetUrl, waitJob, ApiError } from './api.js';
import { h, clear, fmtDuration, fmtMoney, toast, notice, modal, select } from './ui.js';

const STATUS = {
  queued: 'En cola', running: 'Procesando', succeeded: 'Completado', failed: 'Error', cancelled: 'Cancelado',
};

// Muestra el avance de un trabajo en `container` y resuelve con el trabajo terminado.
export function trackJob(container, job, { title = 'Procesando', estimatedSeconds } = {}) {
  const bar = h('div', { class: 'bar-fill' });
  const msg = h('span', { class: 'job-msg' }, job.message || '');
  const pct = h('span', { class: 'job-pct' }, '0 %');
  const eta = h('small', { class: 'muted' }, estimatedSeconds ? `Tiempo estimado: ${fmtDuration(estimatedSeconds)}` : '');
  const cancel = h('button', { class: 'btn small ghost', onclick: async () => {
    await api(`/api/jobs/${job.id}/cancel`, { method: 'POST' });
    toast('Cancelando…');
  } }, 'Cancelar');
  const box = h('div', { class: 'job' },
    h('div', { class: 'job-head' }, h('strong', {}, title), pct),
    h('div', { class: 'bar' }, bar), h('div', { class: 'job-foot' }, msg, cancel), eta);
  clear(container).append(box);
  const started = Date.now();
  return waitJob(job.id, (j) => {
    const p = Math.round((j.progress || 0) * 100);
    bar.style.width = `${p}%`;
    pct.textContent = `${p} %`;
    msg.textContent = `${STATUS[j.status] || j.status} · ${j.message || ''}`;
    if (j.status === 'running' && j.progress > 0.05) {
      const elapsed = (Date.now() - started) / 1000;
      eta.textContent = `Restante aprox.: ${fmtDuration(elapsed / j.progress - elapsed)}`;
    }
  }).then((j) => {
    cancel.remove();
    box.classList.add(j.status);
    if (j.status === 'failed') {
      clear(box).append(jobError(j, container, { title, estimatedSeconds }));
    } else if (j.status === 'succeeded') {
      eta.textContent = 'Completado';
    }
    return j;
  });
}

function jobError(j, container, opts) {
  const report = j.result?.details?.report;
  return h('div', { class: 'notice error' },
    h('strong', {}, 'No se pudo completar: '), j.error || 'error desconocido',
    report ? qualityReport(report) : null,
    h('div', { class: 'row' }, h('button', { class: 'btn small', onclick: async () => {
      const r = await api(`/api/jobs/${j.id}/retry`, { method: 'POST' });
      trackJob(container, r, opts);
    } }, 'Reintentar')));
}

export function qualityReport(rep) {
  if (!rep) return null;
  return h('div', { class: 'quality' },
    rep.blocking?.length ? h('div', { class: 'notice error' }, h('strong', {}, 'Problemas que impiden procesar'),
      h('ul', {}, rep.blocking.map(b => h('li', {}, b)))) : null,
    rep.warnings?.length ? h('div', { class: 'notice warn' }, h('strong', {}, 'Advertencias de calidad'),
      h('ul', {}, rep.warnings.map(b => h('li', {}, b)))) : null,
    !rep.blocking?.length && !rep.warnings?.length ? h('div', { class: 'notice ok' }, 'El material parece adecuado.') : null,
    rep.metrics ? h('small', { class: 'muted' },
      `Rostro visible: ${Math.round((rep.metrics.coverage || 0) * 100)} % · tamaño mediano: ${rep.metrics.median_face_px ?? '—'} px`) : null);
}

export async function videoPlayer(assetId, { poster, muted = false, className = '' } = {}) {
  const v = h('video', { class: `player ${className}`, controls: true, playsinline: true, preload: 'metadata', muted });
  if (poster) v.poster = poster;
  v.src = await assetUrl(assetId);
  return v;
}

export async function audioPlayer(assetId) {
  const a = h('audio', { controls: true, preload: 'metadata' });
  a.src = await assetUrl(assetId);
  return a;
}

export async function img(assetId, alt = '') {
  const i = h('img', { alt, loading: 'lazy' });
  i.src = await assetUrl(assetId);
  return i;
}

export const STEPS = [
  ['personajes', 'Personajes'], ['guion', 'Guion'], ['escenas', 'Escenas'], ['editor', 'Editor'],
  ['vista', 'Vista previa'], ['exportar', 'Exportar'],
];

export function stepper(pid, current) {
  return h('nav', { class: 'stepper', 'aria-label': 'Pasos del proyecto' },
    STEPS.map(([key, label], i) => h('a', {
      href: `#/proyecto/${pid}/${key}`, class: key === current ? 'active' : '',
      'aria-current': key === current ? 'step' : null,
    }, h('span', { class: 'n' }, i + 1), h('span', { class: 't' }, label))));
}

// Llama a un endpoint de generación. Si el servidor responde 402 «confirmar_coste» (proveedor de
// pago), muestra el coste estimado y repite la petición con confirm_cost sólo si el usuario acepta.
export async function withCost(path, body = {}, what = 'Esta generación') {
  try {
    return await api(path, { method: 'POST', body });
  } catch (e) {
    if (!(e instanceof ApiError) || e.code !== 'confirmar_coste') throw e;
    const est = e.details?.estimate || {};
    const ok = await modal({ title: 'Confirmar coste', body: h('div', {},
      h('p', {}, `${what} usa un proveedor de pago.`),
      h('dl', { class: 'kv' }, h('dt', {}, 'Coste estimado'), h('dd', {}, h('strong', {}, fmtMoney(est.cost_usd || 0))),
        est.seconds ? [h('dt', {}, 'Duración del clip'), h('dd', {}, `${est.seconds} s`)] : null,
        est.estimated_wait_seconds ? [h('dt', {}, 'Espera aprox.'), h('dd', {}, fmtDuration(est.estimated_wait_seconds))] : null),
      h('small', { class: 'muted' }, 'Importe orientativo según las tarifas configuradas en el servidor; la factura real es la del proveedor.')),
    actions: [{ label: 'Cancelar', value: false }, { label: 'Confirmar y generar', value: true, kind: 'primary' }] });
    if (!ok) return null;
    return api(path, { method: 'POST', body: { ...body, confirm_cost: true } });
  }
}

export function styleSelect(styles, value, onchange) {
  return select(styles.map(s => [s.key, s.label]), value, onchange);
}

export function providerNote(caps) {
  const local = [];
  if (caps.llm.provider !== 'anthropic') local.push('guion');
  if (caps.image.provider !== 'fal') local.push('imágenes');
  if (caps.video.provider !== 'fal') local.push('vídeo');
  if (!local.length) return null;
  return notice('warn', h('strong', {}, 'Modo local gratuito: '),
    `${local.join(', ')} se generan sin IA generativa (filtros de imagen, composición y animática con movimiento de cámara). `,
    'Sirve para probar el flujo; para resultados reales el administrador debe configurar ANTHROPIC_API_KEY y FAL_KEY.');
}
