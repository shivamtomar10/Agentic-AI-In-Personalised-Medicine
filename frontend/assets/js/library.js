import { el, clear, $, tag, day, words } from './dom.js';
import { api } from './api.js';

const SOURCES = { openfda: 'Drug label or FDA notice', guideline: 'Clinical guideline', pubmed: 'PubMed', europe_pmc: 'Europe PMC', rxnorm: 'RxNorm', ddinter: 'DDInter' };
const input = $('#q'), list = $('#sugg'), out = $('#results');
let items = [], active = -1, timer;

// ---- autocomplete (ARIA combobox)
function closeList() { list.hidden = true; input.setAttribute('aria-expanded', 'false'); input.removeAttribute('aria-activedescendant'); active = -1; }
function setActive(i) {
  active = i;
  [...list.children].forEach((li, n) => li.setAttribute('aria-selected', String(n === i)));
  if (i >= 0) input.setAttribute('aria-activedescendant', 'opt' + i); else input.removeAttribute('aria-activedescendant');
}
input.addEventListener('input', () => {
  clearTimeout(timer);
  const q = input.value.trim();
  if (q.length < 2) return closeList();
  timer = setTimeout(async () => {
    try {
      const d = await api('/api/v1/knowledge/suggest?q=' + encodeURIComponent(q));
      items = d.suggestions; clear(list);
      if (!items.length) return closeList();
      items.forEach((s, i) => list.append(el('li', { role: 'option', id: 'opt' + i, 'aria-selected': 'false', onmousedown: (e) => { e.preventDefault(); pick(s.term); } }, el('span', { text: s.term }), el('small', { text: s.kind }))));
      list.hidden = false; input.setAttribute('aria-expanded', 'true'); active = -1;
    } catch { closeList(); }
  }, 140);
});
input.addEventListener('keydown', (e) => {
  if (list.hidden) return;
  if (e.key === 'ArrowDown') { e.preventDefault(); setActive((active + 1) % items.length); }
  else if (e.key === 'ArrowUp') { e.preventDefault(); setActive((active - 1 + items.length) % items.length); }
  else if (e.key === 'Enter' && active >= 0) { e.preventDefault(); pick(items[active].term); }
  else if (e.key === 'Escape') closeList();
});
input.addEventListener('blur', () => setTimeout(closeList, 100));
function pick(term) { input.value = term; closeList(); go(term); }

// ---- search + render
$('#lib-form').addEventListener('submit', (e) => { e.preventDefault(); closeList(); go(input.value.trim()); });
window.addEventListener('popstate', () => { const q = new URLSearchParams(location.search).get('q'); if (q) { input.value = q; search(q); } else clear(out); });
function go(q) { if (q.length < 2) return; history.pushState({}, '', '/library?q=' + encodeURIComponent(q)); search(q); }

function medicine(m) {
  const box = el('article', { class: 'entry' }, el('h3', { text: m.name }));
  if (m.brand_names.length) box.append(el('div', { class: 'meta', text: 'Also sold as: ' + m.brand_names.join(', ') }));
  if (m.classes.length) box.append(el('div', {}, m.classes.map(c => tag(c, 'info'))));
  const dl = el('dl');
  if (m.allergy_note) {
    dl.append(el('dt', { text: 'Allergy' }), el('dd', { text: 'Same class: ' + m.allergy_note.same_class.join(', ') + '. Possible cross-reactivity: ' + (m.allergy_note.possible_cross_reactivity.join(', ') || 'none listed') + '.' }));
  }
  if (m.interactions.length) dl.append(el('dt', { text: 'Interactions' }), el('dd', {}, el('ul', { style: 'margin:0;padding-left:1.1rem' }, m.interactions.map(i => el('li', {}, tag(i.severity, i.severity), ' with ' + i.with + ': ' + i.effect)))));
  if (dl.children.length) box.append(dl);
  if (!m.classes.length && !m.allergy_note && !m.interactions.length) box.append(el('p', { class: 'muted', text: 'No further detail in this library.' }));
  return box;
}
function evidence(d) {
  const box = el('article', { class: 'entry' }, el('h3', { text: d.title }),
    el('div', { class: 'meta', text: (SOURCES[d.source] || words(d.source)) + (d.publication_date ? ', ' + d.publication_date : ', undated') + (d.verbatim ? '' : '. Paraphrase of the source') }));
  d.snippets.forEach(s => box.append(el('blockquote', { text: s })));
  if (d.url) box.append(el('a', { href: d.url, target: '_blank', rel: 'noopener noreferrer', text: 'Open the original source' }));
  return box;
}
async function search(q) {
  clear(out).append(el('p', { class: 'muted', text: 'Searching...' }));
  document.title = q + ' | Library';
  try {
    const d = await api('/api/v1/knowledge/search?q=' + encodeURIComponent(q));
    clear(out);
    if (!d.medicines.length && !d.evidence.length) {
      out.append(el('div', { class: 'empty' }, el('h3', { text: 'No results for "' + q + '"' }), el('p', { text: 'The library is small. Try a medicine name (metformin), a class (NSAID), or a topic (potassium, kidney function).' })));
      return;
    }
    if (d.medicines.length) { out.append(el('div', { class: 'results-h' }, el('h2', { text: 'Medicines' }), el('span', { class: 'muted', text: String(d.medicines.length) }))); d.medicines.forEach(m => out.append(medicine(m))); }
    if (d.evidence.length) { out.append(el('div', { class: 'results-h' }, el('h2', { text: 'Guidance and evidence' }), el('span', { class: 'muted', text: String(d.evidence.length) }))); d.evidence.forEach(x => out.append(evidence(x))); }
  } catch (e) { clear(out).append(el('div', { class: 'notice bad', role: 'alert', text: e.message })); }
}
const initial = new URLSearchParams(location.search).get('q');
if (initial) { input.value = initial; search(initial); }
