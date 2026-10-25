// Shared form helpers: field-level errors that are announced, and busy buttons.
import { el, $ } from './dom.js';

export function clearErrors(form) {
  form.querySelectorAll('.field-error').forEach(n => n.remove());
  form.querySelectorAll('[aria-invalid]').forEach(n => { n.removeAttribute('aria-invalid'); n.removeAttribute('aria-describedby'); });
}
export function showError(form, field, message) {
  const input = field && form.querySelector(`[name="${field}"]`);
  if (input) {
    input.setAttribute('aria-invalid', 'true');
    const id = 'err-' + field;
    input.setAttribute('aria-describedby', id);
    (input.closest('.field') || input.parentElement).append(el('div', { class: 'field-error', id, text: message }));
    input.focus();
    return true;
  }
  return false;
}
export function formMessage(form, kind, text) {
  const box = form.querySelector('#form-msg') || $('#form-msg');
  box.replaceChildren(el('div', { class: 'notice ' + kind, role: kind === 'bad' ? 'alert' : 'status' }, text));
  box.focus();
}
export async function submitting(button, fn) {
  const label = button.textContent; button.disabled = true; button.textContent = 'Working...';
  try { return await fn(); } finally { button.disabled = false; button.textContent = label; }
}
export function values(form) { const o = {}; new FormData(form).forEach((v, k) => { o[k] = v; }); return o; }
