// Cliente de la API. Gestiona el token, los errores y las subidas con progreso.

const TOKEN_KEY = 'estudio.token';

export class ApiError extends Error {
  constructor(status, body) {
    super(body?.message || `Error ${status}`);
    this.status = status;
    this.code = body?.error;
    this.details = body?.details || {};
  }
}

export const auth = {
  get token() { try { return localStorage.getItem(TOKEN_KEY); } catch { return null; } },
  set token(v) { try { v ? localStorage.setItem(TOKEN_KEY, v) : localStorage.removeItem(TOKEN_KEY); } catch { /* sin almacenamiento */ } },
};

let onUnauthorized = () => {};
export function setUnauthorizedHandler(fn) { onUnauthorized = fn; }

export async function api(path, { method = 'GET', body, form, signal } = {}) {
  const headers = {};
  if (auth.token) headers.Authorization = `Bearer ${auth.token}`;
  let payload;
  if (form) payload = form;
  else if (body !== undefined) { headers['Content-Type'] = 'application/json'; payload = JSON.stringify(body); }
  let res;
  try {
    res = await fetch(path, { method, headers, body: payload, signal });
  } catch (e) {
    throw new ApiError(0, { message: 'Sin conexión con el servidor. Compruebe su red.' });
  }
  const text = await res.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch { data = { message: text }; }
  if (!res.ok) {
    if (res.status === 401 && !path.startsWith('/api/auth/login')) onUnauthorized();
    throw new ApiError(res.status, data);
  }
  return data;
}

export function upload(path, formData, onProgress) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open('POST', path);
    if (auth.token) xhr.setRequestHeader('Authorization', `Bearer ${auth.token}`);
    xhr.upload.onprogress = (e) => { if (e.lengthComputable) onProgress?.(e.loaded / e.total); };
    xhr.onload = () => {
      let data = null;
      try { data = JSON.parse(xhr.responseText); } catch { data = { message: xhr.responseText }; }
      if (xhr.status >= 200 && xhr.status < 300) resolve(data);
      else {
        if (xhr.status === 401) onUnauthorized();
        reject(new ApiError(xhr.status, data));
      }
    };
    xhr.onerror = () => reject(new ApiError(0, { message: 'Se interrumpió la subida. Compruebe su conexión.' }));
    xhr.send(formData);
  });
}

const urlCache = new Map();
export async function assetUrl(id, download = false) {
  const key = `${id}:${download}`;
  const hit = urlCache.get(key);
  if (hit && hit.until > Date.now()) return hit.url;
  const r = await api(`/api/assets/${id}/url${download ? '?download=true' : ''}`);
  urlCache.set(key, { url: r.url, until: Date.now() + (r.expires_in - 120) * 1000 });
  return r.url;
}

// Sondeo de un trabajo hasta que termina. onUpdate recibe el estado en cada consulta.
export async function waitJob(jobId, onUpdate, { interval = 1200, signal } = {}) {
  for (;;) {
    if (signal?.aborted) throw new DOMException('cancelado', 'AbortError');
    const j = await api(`/api/jobs/${jobId}`);
    onUpdate?.(j);
    if (['succeeded', 'failed', 'cancelled'].includes(j.status)) return j;
    await new Promise(r => setTimeout(r, j.status === 'queued' ? interval * 1.5 : interval));
  }
}

let capsPromise = null;
export function capabilities(refresh = false) {
  if (!capsPromise || refresh) capsPromise = api('/api/system/capabilities').catch(e => { capsPromise = null; throw e; });
  return capsPromise;
}
