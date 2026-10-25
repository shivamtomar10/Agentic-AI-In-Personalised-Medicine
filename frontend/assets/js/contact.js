import { el, $ } from './dom.js';
import { api, ApiError } from './api.js';
import { clearErrors, showError, formMessage, submitting, values } from './forms.js';

const form = $('#contact-form');
form.addEventListener('submit', async (e) => {
  e.preventDefault(); clearErrors(form);
  const v = values(form);
  if (!v.name.trim()) return showError(form, 'name', 'Enter your name.');
  if (!/^\S+@\S+\.\S+$/.test(v.email)) return showError(form, 'email', 'Enter a valid email address, like name@example.com.');
  if (v.message.trim().length < 10) return showError(form, 'message', 'Write at least 10 characters.');
  await submitting(form.querySelector('button[type=submit]'), async () => {
    try {
      const r = await api('/api/contact', { method: 'POST', body: { name: v.name, email: v.email, topic: v.topic, message: v.message, website: v.website || '' } });
      form.replaceChildren(el('div', { class: 'notice ok', role: 'status', tabindex: '-1', id: 'done' }, el('p', {}, el('strong', { text: 'Message sent. ' }), 'Your reference is ', el('strong', { text: r.reference }), '.'), el('p', { class: 'fine', text: 'Keep it if you need to follow up.' })));
      $('#done').focus();
    } catch (err) {
      if (!(err instanceof ApiError) || !err.field || !showError(form, err.field, err.message)) formMessage(form, 'bad', err.message);
    }
  });
});
