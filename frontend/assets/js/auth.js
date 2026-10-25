import { el, clear, $, $$ } from './dom.js';
import { api, ApiError, adoptSession, safeNext, siteConfig } from './api.js';
import { clearErrors, showError, formMessage, submitting, values } from './forms.js';

const page = $('[data-auth]').dataset.auth;
const form = $('#form');
const params = new URLSearchParams(location.search);

// ---- shared: show/hide password, strength meter
$$('.pw-toggle').forEach(b => b.addEventListener('click', () => {
  const i = document.getElementById(b.getAttribute('aria-controls')), show = i.type === 'password';
  i.type = show ? 'text' : 'password'; b.textContent = show ? 'Hide' : 'Show'; b.setAttribute('aria-pressed', String(show));
}));
const meter = $('#meter');
if (meter) {
  const pw = $('#password') || $('#new_password');
  pw.addEventListener('input', () => {
    const n = pw.value.length, ok = n >= 10, strong = n >= 16;
    meter.style.width = Math.min(100, (n / 16) * 100) + '%';
    meter.style.background = strong ? 'var(--ok)' : ok ? 'var(--change-line)' : 'var(--bad)';
    $('#meter-label').textContent = !n ? '' : strong ? 'Strong length' : ok ? 'Acceptable. Longer is stronger.' : `${10 - n} more character${10 - n === 1 ? '' : 's'} needed`;
  });
}

function fail(err) {
  if (err instanceof ApiError && err.field && showError(form, err.field, err.message)) return;
  formMessage(form, 'bad', err.message || 'Something went wrong. Try again.');
}
function done(d, roleHint) {
  adoptSession(d);
  const home = d.redirect, wanted = safeNext(params.get('next'), home);
  location.assign(wanted.startsWith(home) ? wanted : home);
}

// ---- login
if (page === 'login') {
  let role = params.get('role') === 'clinician' ? 'clinician' : 'patient';
  const ASIDE = {
    patient: ['Your patient portal', ['Send a check-in and see what was flagged.', 'Follow your results over time.', 'See what your clinician decided, and why.'], '/register', 'Create a patient account'],
    clinician: ['Your review queue', ['See each case with the changes it found and the evidence behind it.', 'Approve, modify or reject with a written rationale.', 'Your decisions are recorded under your staff ID.'], '/register/clinician', 'Register as a clinician'],
  };
  let demo = null;
  const paint = () => {
    $$('.seg button').forEach(b => b.setAttribute('aria-selected', String(b.dataset.role === role)));
    const [h, list, href, label] = ASIDE[role];
    $('#aside-h').textContent = h; clear($('#aside-list')).append(...list.map(t => el('li', { text: t })));
    $('#alt-link').href = href; $('#alt-link').textContent = label;
    const box = $('#demo');
    if (demo) {
      const acc = demo.demo_accounts[role];
      box.hidden = false; clear(box).append(el('p', {}, el('strong', { text: 'Demo server. ' }), 'Try the built-in ' + role + ' account:'),
        el('p', { class: 'fine', style: 'margin:0 0 .5rem' }, acc.email, el('br'), acc.password),
        el('button', { class: 'btn btn-quiet btn-sm', type: 'button', text: 'Fill in these details', onclick: () => { $('#email').value = acc.email; $('#password').value = acc.password; $('#password').focus(); } }));
    }
  };
  $$('.seg button').forEach(b => b.addEventListener('click', () => { role = b.dataset.role; clearErrors(form); paint(); }));
  siteConfig().then(c => { if (c.demo_mode && c.demo_accounts) demo = c; paint(); });
  paint();
  form.addEventListener('submit', async (e) => {
    e.preventDefault(); clearErrors(form);
    const v = values(form);
    if (!v.email.trim()) return showError(form, 'email', 'Enter your email address.');
    if (!v.password) return showError(form, 'password', 'Enter your password.');
    await submitting(form.querySelector('button[type=submit]'), async () => {
      try { done(await api('/api/auth/login', { method: 'POST', body: { email: v.email, password: v.password, role } })); }
      catch (err) { formMessage(form, 'bad', err.message); }
    });
  });
}

// ---- registration (patient and clinician)
if (page === 'register' || page === 'register-clinician') {
  if (page === 'register-clinician') siteConfig().then(c => {
    if (c.demo_mode && c.demo_accounts) {
      const box = $('#demo'); box.hidden = false;
      box.append(el('p', {}, el('strong', { text: 'Demo server. ' }), 'The invite code is ', el('code', { text: c.demo_accounts.clinician_invite_code }), '.'),
        el('button', { class: 'btn btn-quiet btn-sm', type: 'button', text: 'Fill it in', onclick: () => { $('#invite_code').value = c.demo_accounts.clinician_invite_code; } }));
    }
  });
  form.addEventListener('submit', async (e) => {
    e.preventDefault(); clearErrors(form);
    const v = values(form); v.accept_terms = $('#accept_terms').checked;
    const need = page === 'register' ? [['display_name', 'Enter your name.'], ['email', 'Enter your email address.'], ['birth_date', 'Enter your date of birth.'], ['gender', 'Choose an option.'], ['password', 'Choose a password.']]
      : [['display_name', 'Enter your name.'], ['email', 'Enter your work email.'], ['staff_id', 'Enter your staff ID.'], ['invite_code', 'Enter the invite code.'], ['password', 'Choose a password.']];
    for (const [f, m] of need) if (!String(v[f] || '').trim()) return showError(form, f, m);
    if (!v.accept_terms) return showError(form, 'accept_terms', 'Please accept the Terms and Privacy Policy.');
    await submitting(form.querySelector('button[type=submit]'), async () => {
      try { done(await api(page === 'register' ? '/api/auth/register' : '/api/auth/register-clinician', { method: 'POST', body: v })); } catch (err) { fail(err); }
    });
  });
}

// ---- forgot password
if (page === 'forgot') {
  siteConfig().then(c => { if (c.demo_mode) $('#demo').hidden = false; });
  form.addEventListener('submit', async (e) => {
    e.preventDefault(); clearErrors(form);
    const v = values(form);
    if (!/^\S+@\S+\.\S+$/.test(v.email)) return showError(form, 'email', 'Enter a valid email address.');
    await submitting(form.querySelector('button[type=submit]'), async () => {
      try { const r = await api('/api/auth/forgot-password', { method: 'POST', body: { email: v.email } }); formMessage(form, 'ok', r.message); } catch (err) { fail(err); }
    });
  });
}

// ---- reset password
if (page === 'reset') {
  const token = params.get('token') || '';
  (async () => {
    let valid = false;
    try { valid = token && (await api('/api/auth/reset-password/check?token=' + encodeURIComponent(token))).valid; } catch { /* treat as invalid */ }
    $('#bad-link').hidden = valid; form.hidden = !valid;
  })();
  form.addEventListener('submit', async (e) => {
    e.preventDefault(); clearErrors(form);
    const v = values(form);
    if (!v.new_password) return showError(form, 'new_password', 'Choose a password.');
    await submitting(form.querySelector('button[type=submit]'), async () => {
      try { await api('/api/auth/reset-password', { method: 'POST', body: { token, new_password: v.new_password } }); location.assign('/login?reset=1'); } catch (err) { fail(err); }
    });
  });
}
if (params.get('reset') === '1' && page === 'login') formMessage(form, 'ok', 'Password updated. Sign in with your new password.');

if (params.get('expired') === '1' && page === 'login') formMessage(form, 'warn', 'You were signed out because your session ended. Sign in to continue.');
