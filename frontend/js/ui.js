// Utilidades de interfaz: creación de nodos, avisos, diálogos modales y formatos.

export function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === undefined || v === null || v === false) continue;
    if (k === 'class') el.className = v;
    else if (k === 'style' && typeof v === 'object') Object.assign(el.style, v);
    else if (k.startsWith('on') && typeof v === 'function') el.addEventListener(k.slice(2), v);
    else if (k === 'html') el.innerHTML = v; // sólo con contenido propio, nunca datos del usuario
    else if (v === true) el.setAttribute(k, '');
    else el.setAttribute(k, v);
  }
  for (const c of children.flat(Infinity)) {
    if (c === null || c === undefined || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return el;
}

export function clear(el) { while (el.firstChild) el.removeChild(el.firstChild); return el; }

export function toast(message, kind = 'info', ms = 4500) {
  const box = document.getElementById('toasts');
  const t = h('div', { class: `toast ${kind}`, role: kind === 'error' ? 'alert' : 'status' }, message);
  box.append(t);
  setTimeout(() => t.classList.add('out'), ms);
  setTimeout(() => t.remove(), ms + 400);
}

export function modal({ title, body, actions = [], wide = false, dismissable = true }) {
  return new Promise((resolve) => {
    const root = document.getElementById('modal-root');
    const close = (v) => { root.classList.remove('open'); clear(root); resolve(v); };
    const dlg = h('div', { class: `modal ${wide ? 'wide' : ''}`, role: 'dialog', 'aria-modal': 'true', 'aria-label': title },
      h('h2', {}, title),
      h('div', { class: 'modal-body' }, body),
      h('div', { class: 'modal-actions' },
        actions.map(a => h('button', {
          class: `btn ${a.kind || ''}`, onclick: async () => {
            if (a.validate && !(await a.validate())) return;
            close(a.value);
          },
        }, a.label))));
    clear(root).append(h('div', { class: 'backdrop', onclick: () => dismissable && close(null) }), dlg);
    root.classList.add('open');
    dlg.querySelector('button')?.focus();
  });
}

export async function confirmDialog(title, text, okLabel = 'Aceptar', kind = 'primary') {
  const v = await modal({ title, body: h('p', {}, text), actions: [
    { label: 'Cancelar', value: false }, { label: okLabel, value: true, kind }] });
  return v === true;
}

export function fmtTime(s) {
  if (s === null || s === undefined || isNaN(s)) return '—';
  const m = Math.floor(s / 60);
  const sec = s - m * 60;
  return `${m}:${sec.toFixed(1).padStart(4, '0')}`;
}

export function fmtDuration(s) {
  if (!s && s !== 0) return '—';
  if (s < 60) return `${Math.round(s)} s`;
  const m = Math.floor(s / 60);
  return `${m} min ${Math.round(s % 60)} s`;
}

export function fmtDate(ts) {
  return new Date(ts * 1000).toLocaleString('es-ES', { dateStyle: 'medium', timeStyle: 'short' });
}

export function fmtMoney(usd) { return `${usd.toFixed(2)} USD`; }

export function fmtBytes(b) {
  if (!b) return '—';
  const u = ['B', 'KB', 'MB', 'GB'];
  let i = 0;
  while (b >= 1024 && i < u.length - 1) { b /= 1024; i++; }
  return `${b.toFixed(i ? 1 : 0)} ${u[i]}`;
}

export function field(label, input, help) {
  return h('label', { class: 'field' }, h('span', { class: 'label' }, label), input,
    help ? h('small', { class: 'help' }, help) : null);
}

export function checkbox(label, checked = false, onchange) {
  const input = h('input', { type: 'checkbox', checked, onchange: (e) => onchange?.(e.target.checked) });
  return { el: h('label', { class: 'check' }, input, h('span', {}, label)), input };
}

export function select(options, value, onchange) {
  const s = h('select', { onchange: (e) => onchange?.(e.target.value) },
    options.map(([v, l]) => h('option', { value: v, selected: String(v) === String(value) }, l)));
  return s;
}

export function notice(kind, ...content) {
  return h('div', { class: `notice ${kind}` }, ...content);
}

export function spinner(text = 'Cargando…') {
  return h('div', { class: 'loading' }, h('span', { class: 'spin' }), text);
}

export function debounce(fn, ms) {
  let t;
  return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); };
}
