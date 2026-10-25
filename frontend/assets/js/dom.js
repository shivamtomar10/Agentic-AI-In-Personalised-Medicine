// Tiny DOM helpers. Every value from the server goes in through textContent / createTextNode (never innerHTML),
// so a hostile string in a record cannot become markup.
export function el(tag, attrs = {}, ...kids) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k === 'class') n.className = v;
    else if (k === 'text') n.textContent = v;
    else if (k.startsWith('on') && typeof v === 'function') n.addEventListener(k.slice(2), v);
    else if (k === 'value') n.value = v;
    else n.setAttribute(k, v === true ? '' : v);
  }
  for (const c of kids.flat(Infinity)) if (c != null && c !== false) n.append(c.nodeType ? c : document.createTextNode(String(c)));
  return n;
}
export const $ = (sel, root = document) => root.querySelector(sel);
export const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
export const clear = (n) => { while (n.firstChild) n.removeChild(n.firstChild); return n; };
export const tag = (text, cls = '') => el('span', { class: 'tag ' + cls, text });
export const fmt = (x, d = 2) => (x == null ? 'n/a' : typeof x === 'number' ? String(Number(x.toFixed(d))) : x);
export const words = (s) => String(s || '').replace(/_/g, ' ');
export const when = (iso) => (iso ? new Date(iso).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' }) : '');
export const day = (iso) => (iso ? new Date(iso).toLocaleDateString(undefined, { dateStyle: 'medium' }) : '');
export function notice(kind, ...kids) { return el('div', { class: 'notice ' + kind, role: kind === 'bad' ? 'alert' : 'status' }, ...kids); }
export function table(head, rows) {
  return el('div', { class: 'table-wrap' }, el('table', {}, el('thead', {}, el('tr', {}, head.map(h => el('th', { scope: 'col', text: h })))),
    el('tbody', {}, rows.map(r => el('tr', {}, r.map(c => el('td', {}, c)))))));
}
