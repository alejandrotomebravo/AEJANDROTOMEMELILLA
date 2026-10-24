// Componentes reutilizables: progreso de trabajos, reproductor y pasos del asistente.
import { api, assetUrl, waitJob } from './api.js';
import { h, clear, fmtDuration, toast, checkbox, field, notice } from './ui.js';

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
  ['internet', 'Internet'], ['archivos', 'Archivos'], ['escenas', 'Escenas'], ['rostros', 'Rostros'],
  ['dialogos', 'Diálogos'], ['labios', 'Labios'], ['editor', 'Editor'], ['vista', 'Vista previa'], ['exportar', 'Exportar'],
];

export function stepper(pid, current) {
  return h('nav', { class: 'stepper', 'aria-label': 'Pasos del proyecto' },
    STEPS.map(([key, label], i) => h('a', {
      href: `#/proyecto/${pid}/${key}`, class: key === current ? 'active' : '',
      'aria-current': key === current ? 'step' : null,
    }, h('span', { class: 'n' }, i + 1), h('span', { class: 't' }, label))));
}

// Etiqueta de licencia con sus condiciones concretas.
export function licenseBox(lic) {
  if (!lic) return null;
  const yes = (v, si, no) => (v === true ? si : v === false ? no : null);
  const pills = [
    yes(lic.allows_modification, ['Permite modificar', 'ok'], ['NO permite modificar', 'err']),
    yes(lic.allows_commercial, ['Uso comercial', 'ok'], ['Sólo no comercial', 'warn']),
    yes(lic.requires_attribution, ['Exige atribución', 'warn'], ['Sin atribución obligatoria', 'ok']),
    yes(lic.share_alike, ['Compartir igual', 'warn'], null),
  ].filter(Boolean);
  return h('div', { class: 'license' },
    h('div', { class: 'row', style: { marginTop: 0 } }, h('strong', {}, `Licencia: ${lic.name || 'desconocida'}`),
      lic.url ? h('a', { href: lic.url, target: '_blank', rel: 'noopener noreferrer' }, 'ver condiciones') : null),
    h('div', { class: 'row', style: { marginTop: '6px' } }, pills.map(([t, c]) => h('span', { class: `pill ${c}` }, t))),
    lic.notes?.length ? h('ul', { class: 'muted small-list' }, lic.notes.map(n => h('li', {}, n))) : null);
}

// Formulario de procedencia y derechos (archivos locales e Internet).
// Distingue: acceder/descargar ≠ modificar ≠ difundir ≠ imagen de quienes aparecen.
export function provenanceForm(caps, { origin = null, compact = false } = {}) {
  const data = { source_type: origin ? (origin.license?.code && origin.license.code !== 'unknown' ? 'dominio_publico_cc' : '') : '', attested: false,
    can_modify: false, can_distribute: false, third_party_people: false, third_party_people_consent: false };
  const types = h('div', { class: 'list' }, Object.entries(caps.policy.source_types).map(([k, label]) => h('label', { class: 'check list-item' },
    h('input', { type: 'radio', name: `st-${Math.random()}`, value: k, checked: data.source_type === k, onchange: () => { data.source_type = k; extra.hidden = k === 'propio'; } }), h('span', {}, label))));
  const holder = h('input', { type: 'text', maxlength: 200, placeholder: 'Persona u organización titular', value: origin?.author || '' });
  const url = h('input', { type: 'url', placeholder: 'https://… enlace a la licencia o autorización', value: origin?.license?.url || '' });
  const desc = h('input', { type: 'text', maxlength: 500, placeholder: 'Ej.: grabado por mi primo, que me ha dado permiso' });
  const extra = h('div', { hidden: !origin && true }, field('Titular de los derechos', holder), field('Enlace a la licencia o autorización', url));
  const checks = [
    checkbox('Tengo permiso para MODIFICAR este vídeo (poder verlo o descargarlo no lo implica).', false, v => { data.can_modify = v; }).el,
    checkbox('Tengo permiso para DIFUNDIR el resultado según la finalidad del proyecto.', false, v => { data.can_distribute = v; }).el,
  ];
  const peopleOk = checkbox('Cuento con la autorización de esas personas para alterar su imagen o su voz.', false, v => { data.third_party_people_consent = v; });
  peopleOk.el.hidden = true;
  const people = checkbox('En el vídeo aparecen personas identificables distintas de las que autorizo en este proyecto.', false, v => { data.third_party_people = v; peopleOk.el.hidden = !v; });
  const attest = checkbox('Declaro que la información es veraz y que dispongo de los derechos o autorizaciones necesarios.', false, v => { data.attested = v; });
  const el = h('div', { class: 'provenance' },
    origin && origin.license?.code && origin.license.code !== 'unknown'
      ? notice('info', 'La licencia indicada por la fuente se ha rellenado automáticamente. Una licencia abierta no autoriza cualquier uso: revise sus condiciones.')
      : (origin ? notice('warn', 'La fuente no indica licencia: necesita una autorización del titular o acreditar que el material es suyo.') : null),
    h('div', { class: 'field' }, h('span', { class: 'label' }, '¿Con qué derecho usa este vídeo?'), types), extra,
    compact ? null : field('Descripción (opcional)', desc), ...checks, people.el, peopleOk.el, attest.el);
  return { el, value: () => ({ ...data, rights_holder: holder.value, license_url: url.value, description: desc.value }) };
}

export function sourceSelect(sources, value, onchange) {
  const ready = sources.filter(s => s.status === 'ready');
  return h('select', { onchange: (e) => onchange?.(e.target.value) },
    ready.map(s => h('option', { value: s.id, selected: s.id === value }, `${s.label} · ${fmtDuration(s.duration)}${s.kind === 'internet' ? ' · ' + (s.origin.platform || 'Internet') : ''}`)));
}
