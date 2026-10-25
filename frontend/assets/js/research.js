import { el, clear, $, table, fmt, day } from './dom.js';
import { api } from './api.js';

const root = $('#res');
const pct = (x) => Math.round(x * 100) + '%';
(async () => {
  let R;
  try { R = await api('/api/v1/eval/results'); }
  catch (e) { return clear(root).append(el('div', { class: 'notice warn', text: e.status === 404 ? 'No results yet. Run "python run.py eval" and reload this page.' : e.message })); }
  clear(root);
  const m = R.meta, ar = R.adaptation.per_mode, rs = R.stability.per_mode, b = R.boundaries;
  const to = (k) => R.boundaries[k] || {};
  root.append(el('p', { class: 'muted', text: `Run on ${day(m.created_utc)} with ${m.seeds.length} random seeds: ${m.cohort.patients_per_seed} simulated patients per seed, ${m.cohort.encounters_per_patient} visits each. ${m.agents}` }));

  root.append(el('h2', { text: 'The headline results' }));
  root.append(table(['Question', 'Full re-run every time', 'Selective re-planning'], [
    ['Did it react to each of 10 kinds of clinical change?', pct(ar.full_rerun.mean), pct(ar.selective_replan.mean)],
    ['Did the answer stay the same when only irrelevant details changed?', pct(rs.full_rerun.overall), pct(rs.selective_replan.overall)],
    ['When a value flickered back across a threshold within measurement noise, did advice stay steady?', fmt(b.to_safe_stability?.baseline * 100, 0) + '%', fmt(b.to_safe_stability?.selective * 100, 0) + '%'],
    ['When a value crossed a threshold in the unsafe direction, did it respond?', fmt(b.to_unsafe_responsiveness?.baseline * 100, 0) + '%', fmt(b.to_unsafe_responsiveness?.selective * 100, 0) + '%'],
  ]));
  root.append(el('p', { class: 'fine', text: 'Both approaches score the same on correctness because the agents are rule-based and deterministic. The one place selective re-planning differs is stability at thresholds, which is a structural effect. The 10 threshold cases were chosen by the author.' }));

  root.append(el('h2', { style: 'margin-top:2.5rem', text: 'Fewer runs when little changes' }));
  root.append(el('p', { class: 'muted prose', text: 'How much work selective re-planning saves depends entirely on how often a visit contains real change. That rate is an assumption of the simulator, so this is a curve, not a fact about real care.' }));
  const bars = el('div', { class: 'bars panel' });
  for (const e of R.efficiency_sweep) bars.append(el('div', { class: 'row' }, el('span', { text: `${pct(e.signal_fraction)} of visits change` }),
    el('div', { class: 'track' }, el('div', { class: 'fill', style: `width:${Math.max(1, e.reasoning_reduction * 100)}%` })), el('span', { class: 'num', text: '-' + pct(e.reasoning_reduction) })));
  bars.append(el('p', { class: 'fine', style: 'margin:.75rem 0 0', text: 'Bars show the reduction in reasoning-agent runs compared with re-running everything each visit.' }));
  root.append(bars);

  const f = R.faults, nf = R.faults_no_defences;
  root.append(el('h2', { style: 'margin-top:2.5rem', text: 'Catching deliberately injected errors' }));
  root.append(el('p', { class: 'prose', text: `${f.n_injected} faults (such as a fabricated interaction, an inverted lab value, or a made-up citation) were injected. With the safety layers on, ${pct(f.pcr)} were stopped before reaching a clinician. With them off, ${pct(nf.pcr)} were.` }));
  root.append(el('p', { class: 'fine', text: 'The faults were designed by the author and are detectable by checks the author wrote. This shows the checks work on these kinds of fault, not that unseen failures would be caught.' }));

  root.append(el('h2', { style: 'margin-top:2.5rem', text: 'Knowing when to defer' }));
  const c = R.calibration;
  root.append(el('p', { class: 'prose', text: `A learned confidence score separates right from wrong answers better than a fixed one (AUROC ${fmt(c.fixed.auroc)} to ${fmt(c.learned.auroc)}). The average confidence attached to wrong answers fell from ${fmt(c.fixed.mean_conf_incorrect)} to ${fmt(c.learned.mean_conf_incorrect)}, so wrong answers look less sure of themselves.` }));
  root.append(el('p', { class: 'fine', text: 'Where the system is weakest: cases involving medicines outside its 85-drug table were right only about a fifth of the time, and it says "not assessed" for them. Weights were calibrated during evaluation.' }));

  root.append(el('h2', { style: 'margin-top:2.5rem', text: 'Charts' }));
  const figs = [['efficiency_vs_change_rate.png', 'Efficiency against how often true change occurs', 'Fewer agent runs are needed the less often visits contain real change.'],
    ['stability_vs_responsiveness.png', 'Stability against responsiveness', 'Selective re-planning keeps advice steady near thresholds while still responding to unsafe crossings.'],
    ['risk_coverage.png', 'Risk against coverage', 'Answering fewer cases (deferring more) lowers the error rate on those that remain.'],
    ['pcr_ablation.png', 'Error containment with each safety layer removed', 'Validation is the layer that does most of the work in these tests.']];
  const g = el('div', { class: 'figs' });
  for (const [file, alt, cap] of figs) g.append(el('figure', {}, el('img', { src: '/assets/figs/' + file, alt, loading: 'lazy', width: 640, height: 400 }), el('figcaption', { text: cap })));
  root.append(g);
  root.append(el('p', { class: 'fine', style: 'margin-top:2rem', text: 'The full write-up, with ablations and every caveat, is in docs/results.md and docs/limitations.md in the project.' }));
})();
