// Enrutador de la aplicación (hash) y arranque.
import { api, auth, setUnauthorizedHandler } from './api.js';
import { h, clear, toast } from './ui.js';
import { stepper } from './components.js';

import * as login from './screens/login.js';
import * as home from './screens/home.js';
import * as nuevo from './screens/nuevo.js';
import * as importar from './screens/importar.js';
import * as rostros from './screens/rostros.js';
import * as dialogos from './screens/dialogos.js';
import * as labios from './screens/labios.js';
import * as editor from './screens/editor.js';
import * as vista from './screens/vista.js';
import * as exportar from './screens/exportar.js';
import * as ajustes from './screens/ajustes.js';
import * as identidades from './screens/identidades.js';
import * as consentimiento from './screens/consentimiento.js';

const PROJECT_STEPS = { importar, rostros, dialogos, labios, editor, vista, exportar };
const main = document.getElementById('app');
let cleanup = null;

setUnauthorizedHandler(() => {
  auth.token = null;
  if (!location.hash.startsWith('#/consentimiento')) location.hash = '#/entrar';
});

function nav(active) {
  const n = document.getElementById('nav');
  clear(n);
  if (!auth.token) return;
  n.append(
    h('a', { href: '#/', class: active === 'home' ? 'active' : '' }, 'Proyectos'),
    h('a', { href: '#/identidades', class: active === 'ids' ? 'active' : '' }, 'Personas'),
    h('a', { href: '#/ajustes', class: active === 'settings' ? 'active' : '' }, 'Ajustes'));
}

async function route() {
  if (cleanup) { try { cleanup(); } catch { /* nada */ } cleanup = null; }
  const parts = (location.hash.replace(/^#\/?/, '') || '').split('/').filter(Boolean);
  const [root, id, step] = parts;
  clear(main);
  window.scrollTo(0, 0);
  try {
    if (root === 'consentimiento') { nav(); return void (cleanup = await consentimiento.render(main, id)); }
    if (!auth.token) {
      if (root !== 'entrar') { location.hash = '#/entrar'; return; }
      nav(); return void (cleanup = await login.render(main));
    }
    if (root === 'entrar') { location.hash = '#/'; return; }
    if (!root) { nav('home'); return void (cleanup = await home.render(main)); }
    if (root === 'nuevo') { nav('home'); return void (cleanup = await nuevo.render(main)); }
    if (root === 'ajustes') { nav('settings'); return void (cleanup = await ajustes.render(main)); }
    if (root === 'identidades') { nav('ids'); return void (cleanup = await identidades.render(main)); }
    if (root === 'proyecto' && id) {
      nav('home');
      const key = PROJECT_STEPS[step] ? step : 'importar';
      const project = await api(`/api/projects/${id}`);
      const head = h('div', { class: 'project-head' },
        h('div', {}, h('a', { href: '#/', class: 'back' }, '‹ Proyectos'), h('h1', {}, project.name),
          h('span', { class: 'pill' }, project.aspect)),
        stepper(id, key));
      const body = h('section', { class: 'step-body' });
      main.append(head, body);
      cleanup = await PROJECT_STEPS[key].render(body, project);
      return;
    }
    main.append(h('p', {}, 'Página no encontrada.'));
  } catch (e) {
    console.error(e);
    main.append(h('div', { class: 'notice error' }, e.message || 'Error inesperado.'));
  }
}

window.addEventListener('hashchange', route);
route();

if ('serviceWorker' in navigator) {
  navigator.serviceWorker.register('/sw.js').catch(() => {});
}
window.addEventListener('offline', () => toast('Sin conexión: los cambios se guardarán al reconectar.', 'warn'));
