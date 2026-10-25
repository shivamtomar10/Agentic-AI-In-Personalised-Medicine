// Fetch wrapper: same-origin cookies, JSON, CSRF token on writes, readable errors.
let csrf = null;
let meCache = null;

export class ApiError extends Error {
  constructor(status, message, field) { super(message); this.status = status; this.field = field || null; }
}

export async function api(path, { method = 'GET', body } = {}) {
  const headers = { Accept: 'application/json' };
  if (body !== undefined) headers['Content-Type'] = 'application/json';
  if (csrf && method !== 'GET') headers['X-CSRF-Token'] = csrf;
  let r;
  try { r = await fetch(path, { method, headers, credentials: 'same-origin', body: body === undefined ? undefined : JSON.stringify(body) }); }
  catch { throw new ApiError(0, 'Could not reach the server. Check your connection and try again.'); }
  let data = null;
  try { data = await r.json(); } catch { /* not JSON */ }
  if (!r.ok) {
    const d = data && data.detail;
    let msg = typeof d === 'string' ? d : r.statusText || 'Something went wrong';
    let field = data && data.field;
    if (Array.isArray(d) && d.length) {
      const loc = (d[0].loc || []).filter(x => x !== 'body');
      field = field || loc[loc.length - 1];
      msg = d.map(x => x.msg.replace(/^Value error, /, '')).join(' ');
    }
    if (r.status === 401 && !path.startsWith('/api/auth/login')) { meCache = null; window.dispatchEvent(new Event('session-expired')); }
    throw new ApiError(r.status, msg, field);
  }
  return data;
}

export async function me(force = false) {
  if (meCache && !force) return meCache;
  const d = await api('/api/auth/me');
  csrf = d.csrf;
  meCache = d;
  return d;
}
export function adoptSession(d) { csrf = d.csrf; meCache = { authenticated: true, user: d.user, csrf: d.csrf }; }
export function forgetSession() { csrf = null; meCache = null; }
export const siteConfig = (() => { let p; return () => (p ||= api('/api/site/config').catch(() => ({ demo_mode: false }))); })();

// Only ever follow same-site relative paths after sign-in.
export function safeNext(raw, fallback) {
  try { const u = new URL(raw || '', location.origin); if (u.origin === location.origin && u.pathname.startsWith('/') && !u.pathname.startsWith('//')) return u.pathname + u.search; } catch { /* ignore */ }
  return fallback;
}
