import { api, auth } from '../api.js';
import { h, field, toast } from '../ui.js';

export async function render(root) {
  let mode = 'login';
  const email = h('input', { type: 'email', autocomplete: 'email', required: true, placeholder: 'nombre@correo.es' });
  const pass = h('input', { type: 'password', autocomplete: 'current-password', required: true, minlength: 10 });
  const title = h('h1', {}, 'Iniciar sesión');
  const submit = h('button', { class: 'btn primary block', type: 'submit' }, 'Entrar');
  const swap = h('button', { class: 'btn ghost block', type: 'button', onclick: () => {
    mode = mode === 'login' ? 'register' : 'login';
    title.textContent = mode === 'login' ? 'Iniciar sesión' : 'Crear cuenta';
    submit.textContent = mode === 'login' ? 'Entrar' : 'Crear cuenta';
    swap.textContent = mode === 'login' ? '¿No tiene cuenta? Crear una' : 'Ya tengo cuenta';
    pass.autocomplete = mode === 'login' ? 'current-password' : 'new-password';
  } }, '¿No tiene cuenta? Crear una');
  const form = h('form', { class: 'card auth-box', onsubmit: async (e) => {
    e.preventDefault();
    submit.disabled = true;
    try {
      const r = await api(`/api/auth/${mode}`, { method: 'POST', body: { email: email.value, password: pass.value } });
      auth.token = r.token;
      location.hash = '#/';
    } catch (err) {
      toast(err.message, 'error');
    } finally { submit.disabled = false; }
  } },
  title,
  h('p', { class: 'muted' }, 'Cree vídeos personalizados con IA de forma responsable: sólo con material y rostros autorizados, y siempre con aviso visible de contenido sintético.'),
  field('Correo electrónico', email),
  field('Contraseña', pass, 'Mínimo 10 caracteres.'),
  submit, h('div', { style: { height: '8px' } }), swap);
  root.append(form);
}
