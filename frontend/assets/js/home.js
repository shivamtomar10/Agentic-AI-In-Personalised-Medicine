import { el, clear, $ } from './dom.js';
import { api } from './api.js';

// The three visits are the project's own demo patient (src/api/demo.py). The counts and actions below are what the
// pipeline actually produced for it (see tests/test_api.py::test_demo_timeline_rerun_and_audit).
const VISITS = [
  { label: 'January', labs: { eGFR: [72, 'mL/min'], Potassium: [4.4, 'mmol/L'], HbA1c: [6.9, '%'] }, changed: [], alert: false,
    did: 'First record for this patient, so all 8 checks ran.', sent: ['Review ibuprofen: caution for older adults.'] },
  { label: 'April', labs: { eGFR: [71, 'mL/min'], Potassium: [4.5, 'mmol/L'], HbA1c: [6.8, '%'] }, changed: [], alert: false,
    did: 'Values moved by no more than normal measurement variation. Only 4 of 8 checks ran; earlier reasoning was reused after being re-checked.', sent: ['Nothing new. The earlier note about ibuprofen still stands.'] },
  { label: 'July', labs: { eGFR: [24, 'mL/min'], Potassium: [5.8, 'mmol/L'], HbA1c: [9.4, '%'] }, changed: ['eGFR', 'Potassium', 'HbA1c'], alert: true,
    did: 'Kidney function, potassium and HbA1c all crossed safety thresholds, and a penicillin allergy plus amoxicillin appeared. Seven changes detected; all 8 checks ran again.',
    sent: ['Stop metformin: eGFR is below 30. (urgent)', 'Stop amoxicillin: documented penicillin allergy. (urgent)', 'Review lisinopril: potassium is 5.8. (urgent)', '5 more suggestions, 2 critical flags in total.'] },
];
const EGFR = VISITS.map(v => v.labs.eGFR[0]);
let current = 0;

function spark(upto) {
  const W = 300, H = 60, pad = 6, max = 80, min = 0;
  const x = i => pad + i * ((W - 2 * pad) / (EGFR.length - 1)), y = v => H - pad - ((v - min) / (max - min)) * (H - 2 * pad);
  const NS = 'http://www.w3.org/2000/svg';
  const svg = document.createElementNS(NS, 'svg');
  svg.setAttribute('viewBox', `0 0 ${W} ${H}`); svg.setAttribute('class', 'spark'); svg.setAttribute('role', 'img');
  svg.setAttribute('aria-label', 'eGFR over the visits shown: ' + EGFR.slice(0, upto + 1).join(', '));
  const path = (n) => EGFR.slice(0, n + 1).map((v, i) => (i ? 'L' : 'M') + x(i) + ' ' + y(v)).join(' ');
  const mk = (tagName, attrs) => { const n = document.createElementNS(NS, tagName); for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v); return n; };
  svg.append(mk('path', { d: path(EGFR.length - 1), fill: 'none', stroke: 'var(--line-strong)', 'stroke-width': 2, 'stroke-dasharray': '4 4' }));
  svg.append(mk('path', { d: path(upto), fill: 'none', stroke: 'var(--brand)', 'stroke-width': 3, 'stroke-linejoin': 'round' }));
  EGFR.forEach((v, i) => {
    const reached = i <= upto;
    svg.append(mk('circle', { cx: x(i), cy: y(v), r: i === upto ? 5.5 : 4, fill: reached ? (i === upto && VISITS[upto].alert ? 'var(--change)' : 'var(--brand)') : 'var(--surface)', stroke: reached ? 'none' : 'var(--line-strong)', 'stroke-width': 2 }));
  });
  return svg;
}

function draw() {
  const v = VISITS[current];
  const rail = clear($('#rail'));
  VISITS.forEach((x, i) => rail.append(el('button', { type: 'button', role: 'tab', id: 'tab' + i, 'aria-selected': String(i === current), 'aria-controls': 'trace-panel', 'data-alert': String(x.alert), tabindex: i === current ? '0' : '-1',
    onclick: () => { current = i; draw(); }, onkeydown: (e) => { if (e.key === 'ArrowRight') { current = (current + 1) % 3; draw(); $('#tab' + current).focus(); } if (e.key === 'ArrowLeft') { current = (current + 2) % 3; draw(); $('#tab' + current).focus(); } } },
    el('span', { class: 'dot', text: String(i + 1) }), el('span', { text: x.label }))));
  const p = clear($('#trace-panel'));
  p.setAttribute('aria-labelledby', 'tab' + current);
  p.append(el('div', { class: 'labs' }, Object.entries(v.labs).map(([k, [val, unit]]) => el('div', { class: 'lab' + (v.changed.includes(k) ? ' chg' : '') }, el('div', { class: 'k', text: k }), el('div', { class: 'v', text: String(val) }), el('div', { class: 'u', text: unit })))),
    el('div', { class: 'trace-out' }, el('h3', { text: 'What the system did' }), el('p', { text: v.did, style: 'margin:0' }),
      el('h3', { text: 'What the clinician is asked to look at' }), el('ul', {}, v.sent.map(s => el('li', { text: s }))),
      el('h3', { text: 'eGFR so far' }), spark(current)));
}
draw();

// Live figures from the project's own evaluation run.
(async () => {
  const box = $('#home-stats');
  try {
    const R = await api('/api/v1/eval/results');
    const e = R.efficiency_sweep[0], f = R.faults;
    const stat = (b, s) => el('div', { class: 'stat' }, el('b', { text: b }), el('span', { text: s }));
    box.append(
      stat(Math.round(e.reasoning_reduction * 100) + '%', `fewer reasoning runs when only ${Math.round(e.signal_fraction * 100)}% of visits contain a real change`),
      stat(`${Math.round(f.pcr * f.n_injected)} of ${f.n_injected}`, 'deliberately injected errors were stopped before reaching a clinician'),
      stat(`${R.meta.cohort.patients_per_seed * R.meta.seeds.length}`, 'simulated patients across ' + R.meta.cohort.encounters_per_patient + ' visits each'));
  } catch { box.append(el('p', { class: 'muted', text: 'Results will appear here once the evaluation has been run.' })); }
})();
