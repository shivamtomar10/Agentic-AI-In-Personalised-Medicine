import { el, clear, $ } from './dom.js';
import { api, me, forgetSession } from './api.js';

// Mobile menu
const header = $('#site-header'), btn = $('#menu-btn');
if (btn) btn.addEventListener('click', () => { const open = header.classList.toggle('open'); btn.setAttribute('aria-expanded', String(open)); });

// Header account area reflects the real session (the server still enforces everything; this is only display).
(async () => {
  const slot = $('#account-slot');
  if (!slot) return;
  try {
    const d = await me();
    if (!d.authenticated) return;
    const u = d.user, home = u.role === 'clinician' ? '/clinician' : '/portal';
    clear(slot).append(
      el('span', { class: 'who', text: u.display_name }),
      el('a', { class: 'btn btn-quiet btn-sm', href: home, text: u.role === 'clinician' ? 'Review queue' : 'My portal' }),
      el('button', { class: 'btn btn-plain btn-sm', type: 'button', text: 'Sign out', onclick: signOut }));
  } catch { /* keep signed-out links */ }
})();

export async function signOut() {
  try { await api('/api/auth/logout', { method: 'POST' }); } catch { /* cookie may already be gone */ }
  forgetSession();
  location.assign('/');
}

// A 401 from the API on a signed-in page means the session ended (for clinicians: 30 minutes idle). Go back to sign-in, then return here.
window.addEventListener('session-expired', () => {
  const p = location.pathname;
  if (p === '/portal' || p === '/clinician') location.assign('/login?' + (p === '/clinician' ? 'role=clinician&' : '') + 'next=' + encodeURIComponent(p) + '&expired=1');
});
