// Service worker: guarda la interfaz para que la PWA abra sin conexión.
// Nunca almacena respuestas de la API ni archivos multimedia de los usuarios.
const CACHE = 'estudio-ia-v4';
const SHELL = [
  '/', '/manifest.webmanifest', '/static/css/app.css', '/static/js/app.js', '/static/js/api.js', '/static/js/ui.js',
  '/static/js/components.js', '/static/js/history.js', '/static/icons/icon.svg', '/static/icons/icon-192.png',
  ...['login', 'home', 'nuevo', 'personajes', 'guion', 'escenas', 'editor', 'vista', 'exportar', 'ajustes',
    'identidades', 'consentimiento', 'proyectos'].map(s => `/static/js/screens/${s}.js`),
];

self.addEventListener('install', (e) => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', (e) => {
  e.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k))))
    .then(() => self.clients.claim()));
});

self.addEventListener('fetch', (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== 'GET' || url.origin !== location.origin || url.pathname.startsWith('/api/')) return;
  // Red primero (para recibir actualizaciones) y caché como respaldo sin conexión.
  e.respondWith(fetch(e.request).then(res => {
    const copy = res.clone();
    if (res.ok) caches.open(CACHE).then(c => c.put(e.request, copy));
    return res;
  }).catch(() => caches.match(e.request)));
});
