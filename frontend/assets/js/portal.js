import { el, clear, $, $$, tag, fmt, words, when, day, notice } from './dom.js';
import { api, me, ApiError } from './api.js';
import { trend } from './charts.js';
import { clearErrors, showError, submitting } from './forms.js';
import { signOut } from './site.js';

const view = $('#view');
let user = null, poll = null, flash = null;

const STATUS = {
  requires_clinician_review: ['Waiting for a clinician', 'warn'], clinician_approved: ['Approved by a clinician', 'ok'], clinician_modified: ['Approved with changes', 'ok'],
  rejected: ['Not accepted by the clinician', 'bad'], escalated: ['Escalated for urgent attention', 'bad'],
};
const statusTag = (s) => { const [t, k] = STATUS[s] || [words(s), '']; return tag(t, k); };
const stopPoll = () => { if (poll) { clearInterval(poll); poll = null; } };
const heading = (text, sub) => el('header', {}, el('h1', { id: 'page-h', tabindex: '-1', text }), sub ? el('p', { class: 'muted', text: sub }) : null);
function show(title, ...kids) {
  stopPoll(); clear(view).append(...kids);
  document.title = title + ' | My portal';
  $('#page-h')?.focus({ preventScroll: true });
  window.scrollTo(0, 0);
}
const failed = (e) => notice('bad', e.message || String(e));

// ------------------------------------------------------------------ router
const routes = { '': home, 'check-in': checkin, cases, prescriptions, account };
async function route() {
  const [a, b] = location.hash.replace(/^#\/?/, '').split('/');
  $$('.side nav a').forEach(x => (x.dataset.route === (a || '') ? x.setAttribute('aria-current', 'page') : x.removeAttribute('aria-current')));
  try {
    if (a === 'cases' && b) return await caseDetail(decodeURIComponent(b));
    return await (routes[a || ''] || home)();
  } catch (e) {
    if (e instanceof ApiError && e.status === 401) return location.assign('/login?next=/portal');
    show('Something went wrong', heading('Something went wrong'), failed(e));
  }
}
window.addEventListener('hashchange', route);

// ------------------------------------------------------------------ overview
async function home() {
  const [hist, tl] = await Promise.all([api('/api/v1/patient/me/recommendations'), api('/api/v1/patient/me/timeline')]);
  const waiting = hist.items.filter(i => !i.reviewed).length;
  badge(hist.items.filter(i => !i.reviewed).length);
  const latest = hist.items[0], decided = hist.items.find(i => i.reviewed);
  const first = user.display_name.split(' ')[0];
  const cards = el('div', { class: 'cards' },
    el('div', { class: 'card' + (waiting ? ' attn' : '') }, el('h2', { text: 'Waiting for a clinician' }), el('div', { class: 'big', text: String(waiting) }), el('p', { class: 'fine', style: 'margin:.3rem 0 0', text: waiting ? 'A clinician has not looked at these yet.' : 'Nothing is waiting.' })),
    el('div', { class: 'card' }, el('h2', { text: 'Latest decision' }), decided ? [statusTag(decided.status), el('p', { class: 'fine', style: 'margin:.4rem 0 0', text: day(decided.created_at) + ', visit ' + decided.state_version })] : el('p', { class: 'muted', style: 'margin:0', text: 'None yet.' })),
    el('div', { class: 'card' }, el('h2', { text: 'Visits recorded' }), el('div', { class: 'big', text: String(tl.versions.length) }), el('p', { class: 'fine', style: 'margin:.3rem 0 0', text: tl.versions.length ? 'Last on ' + day(tl.versions[tl.versions.length - 1].timestamp) : 'Send your first check-in to start.' })));
  const out = [heading('Hello, ' + first, 'Here is where things stand.'), cards, el('div', { class: 'btn-row' }, el('a', { class: 'btn', href: '#/check-in', text: 'Start a check-in' }), latest ? el('a', { class: 'btn btn-quiet', href: '#/cases/' + encodeURIComponent(latest.recommendation_id), text: 'Open latest case' }) : null, el('a', { class: 'btn btn-quiet', href: '#/prescriptions', text: 'My prescriptions' }))];
  if (tl.versions.length) {
    out.push(el('div', { class: 'section-h' }, el('h2', { text: 'Your results over time' })));
    const series = {};
    for (const v of tl.versions) for (const [k, [val, unit]] of [...Object.entries(v.labs), ...Object.entries(v.vitals)]) (series[k] ||= { unit, vals: [] }).vals.push(val);
    const grid = el('div', { class: 'trends' });
    for (const [k, { unit, vals }] of Object.entries(series)) {
      const last = vals[vals.length - 1], prev = vals[vals.length - 2];
      grid.append(el('div', { class: 'trend' }, el('div', { class: 'k', text: k }), el('div', {}, el('span', { class: 'v', text: fmt(last) }), ' ', el('span', { class: 'u', text: unit })),
        trend(vals, k, unit), el('div', { class: 'd fine', text: prev == null ? 'First reading' : (last === prev ? 'Same as last visit' : (last > prev ? 'Up' : 'Down') + ' from ' + fmt(prev) + ' last visit') })));
    }
    out.push(grid, el('p', { class: 'fine', style: 'margin-top:1rem', text: 'These charts show your own entries. They do not tell you whether a value is healthy. Your clinician does that.' }));
  } else {
    out.push(el('div', { class: 'empty', style: 'margin-top:1.5rem' }, el('h3', { text: 'No check-ins yet' }), el('p', { text: 'Enter your latest results and medicines, and a clinician will review them.' }), el('a', { class: 'btn', href: '#/check-in', text: 'Start your first check-in' })));
  }
  show('Overview', ...out);
}
function badge(n) { const b = $('#waiting-badge'); b.hidden = !n; b.textContent = String(n); }

// ------------------------------------------------------------------ check-in form
const LAB_FIELDS = [['egfr', 'eGFR', 'mL/min/1.73m2'], ['hba1c', 'HbA1c', '%'], ['potassium', 'Potassium', 'mmol/L'], ['creatinine', 'Creatinine', 'mg/dL'], ['alt', 'ALT', 'U/L'], ['sbp', 'Systolic blood pressure', 'mmHg']];
const norm = (k) => k.toLowerCase().replace(/[^a-z0-9]/g, '');
const KEYMAP = { egfr: 'egfr', hba1c: 'hba1c', potassium: 'potassium', creatinine: 'creatinine', alt: 'alt', sbp: 'sbp', systolicbp: 'sbp', systolicbloodpressure: 'sbp' };

async function checkin() {
  const last = (await api('/api/v1/patient/me/last-submission')).submission;
  const ta = (id, label, hint, val) => el('div', { class: 'field' }, el('label', { for: id }, label, hint ? el('span', { class: 'hint', text: hint }) : null), el('textarea', { id, name: id, rows: 3, maxlength: 2000, text: (val || []).join('\n') }));
  const pre = {};
  if (last) for (const [k, v] of Object.entries({ ...last.labs, ...last.vitals })) if (KEYMAP[norm(k)]) pre[KEYMAP[norm(k)]] = v;
  const form = el('form', { id: 'ci', novalidate: true },
    last ? notice('info', el('strong', { text: 'Pre-filled from your last check-in. ' }), 'Update anything that has changed, and clear any result you have not had measured this time.') : null,
    el('div', { class: 'form-sec' }, el('h2', { text: 'Automated Lab Report Extractor (Optional)' }),
      el('p', { text: 'Paste unstructured laboratory findings or clinical report text to automatically extract and pre-fill your check-in results.' }),
      el('div', { class: 'field' }, el('label', { for: 'report_text' }, 'Medical Report Text'), el('textarea', { id: 'report_text', rows: 4, placeholder: 'Paste lab report here, e.g.: eGFR: 45 mL/min/1.73m2, HbA1c: 7.4 %, ALT: 135 U/L, BP: 138/84' })),
      el('div', { id: 'report-msg', style: 'margin-top:.4rem' }),
      el('button', { class: 'btn btn-quiet', type: 'button', text: 'Extract findings & fill form', style: 'margin-top:.4rem', onclick: async () => {
        const txt = $('#report_text').value;
        const msg = $('#report-msg'); clear(msg);
        if (!txt || txt.length < 5) return msg.append(notice('warn', 'Please paste clinical report text first.'));
        try {
          const res = await api('/api/v1/patient/extract-report', { method: 'POST', body: { text: txt } });
          for (const [k, v] of Object.entries(res.labs)) if (form.elements[k]) form.elements[k].value = v.value;
          for (const [k, v] of Object.entries(res.vitals)) if (form.elements[k]) form.elements[k].value = v.value;
          if (res.medications.length) form.elements.medications.value = (form.elements.medications.value ? form.elements.medications.value + '\n' : '') + res.medications.join('\n');
          if (res.warnings.length) msg.append(notice('warn', 'Warnings: ' + res.warnings.join(' | ')));
          else msg.append(notice('ok', 'Successfully extracted ' + res.extracted_items.length + ' item(s) from report and updated fields.'));
        } catch(err) { clear(msg).append(notice('bad', err.message)); }
      }})),
    el('div', { class: 'form-sec' }, el('h2', { text: 'What you take and what you have' }), el('p', { text: 'One item per line. Add doses if you know them.' }),
      ta('conditions', 'Health conditions', 'For example: Type 2 diabetes (E11.9)', last?.conditions), ta('medications', 'Medicines', 'For example: Metformin 1000 mg BID', last?.medications), ta('allergies', 'Allergies', 'For example: Penicillin (rash)', last?.allergies)),
    el('div', { class: 'form-sec' }, el('h2', { text: 'Latest results' }), el('p', { text: 'Leave a box empty if it was not measured. Nothing is guessed for you.' }),
      el('div', { class: 'lab-grid' }, LAB_FIELDS.map(([id, label, unit]) => el('div', { class: 'field' }, el('label', { for: id }, label, el('span', { class: 'hint', text: unit })), el('input', { id, name: id, type: 'number', step: 'any', min: '0', inputmode: 'decimal', value: pre[id] ?? '' }))))),
    el('div', { class: 'form-sec' }, el('h2', { text: 'How you feel' }),
      el('div', { class: 'emergency-box' }, el('strong', { text: 'If you have chest pain, trouble breathing, signs of a stroke, or feel you are in danger, call your local emergency number now. ' }), 'This site cannot respond to emergencies.'),
      el('div', { class: 'field' }, el('label', { for: 'chief_complaint' }, 'Main reason for this check-in'), el('input', { id: 'chief_complaint', name: 'chief_complaint', maxlength: 500, placeholder: 'For example: routine follow-up' })),
      el('div', { class: 'field' }, el('label', { for: 'symptoms' }, 'Symptoms, if any'), el('textarea', { id: 'symptoms', name: 'symptoms', maxlength: 1000 })),
      el('details', { class: 'adv' }, el('summary', { text: 'Research option' }), el('div', { class: 'field', style: 'margin-top:.6rem' }, el('label', { for: 'mode' }, 'How the system analyses this check-in'),
        el('select', { id: 'mode', name: 'mode' }, el('option', { value: 'selective_replan', text: 'Selective re-planning: reuses earlier reasoning when little changed' }), el('option', { value: 'full_rerun', text: 'Full re-run: ignores history (baseline for comparison)' }))))),
    el('div', { id: 'ci-msg', tabindex: '-1' }),
    el('div', { class: 'btn-row' }, el('button', { class: 'btn', type: 'submit', text: 'Send check-in' }), el('a', { class: 'btn btn-plain', href: '#/', text: 'Cancel' })));
  form.addEventListener('submit', async (e) => {
    e.preventDefault(); clearErrors(form);
    const num = (id) => { const v = form.elements[id].value; return v === '' ? null : Number(v); };
    const lines = (id) => form.elements[id].value.split('\n').map(s => s.trim()).filter(Boolean);
    const labs = {}, vitals = {};
    for (const id of ['egfr', 'hba1c', 'potassium', 'creatinine', 'alt']) if (num(id) != null) labs[id] = num(id);
    if (num('sbp') != null) vitals.systolic_bp = num('sbp');
    const body = { conditions: lines('conditions'), medications: lines('medications'), allergies: lines('allergies'), labs, vitals, chief_complaint: form.elements.chief_complaint.value, symptoms: form.elements.symptoms.value };
    await submitting(form.querySelector('button[type=submit]'), async () => {
      try {
        const r = await api('/api/v1/patient/analyse?mode=' + form.elements.mode.value, { method: 'POST', body });
        flash = r.emergency ? null : 'Check-in sent. A clinician will review it.';
        location.hash = '#/cases/' + encodeURIComponent(r.recommendation_id);
      } catch (err) {
        const box = $('#ci-msg'); clear(box).append(failed(err)); box.focus();
        if (err.field && err.field !== 'body') showError(form, err.field, err.message);
      }
    });
  });
  show('New check-in', heading('New check-in', 'Tell us what has changed since last time.'), form);
}

// ------------------------------------------------------------------ prescriptions
async function prescriptions() {
  const d = await api('/api/v1/patient/me/prescriptions');
  if (!d.prescriptions.length) return show('My prescriptions', heading('My prescriptions'), el('div', { class: 'empty' }, el('h3', { text: 'No signed prescriptions yet' }), el('p', { text: 'Once your clinician approves and signs a prescription, it will appear here.' }), el('a', { class: 'btn btn-quiet', href: '#/', text: 'Back to overview' })));
  const items = d.prescriptions.map(rx => {
    const card = el('div', { class: 'card', style: 'margin-bottom:1rem' },
      el('div', { class: 'btn-row' }, el('h2', { style: 'flex:1', text: rx.medication }), tag('Signed', 'ok')),
      el('dl', { class: 'kv', style: 'margin-top:.6rem' },
        el('dt', { text: 'Dosage' }), el('dd', { text: rx.dosage }),
        el('dt', { text: 'Route' }), el('dd', { text: rx.route }),
        el('dt', { text: 'Frequency' }), el('dd', { text: rx.frequency }),
        el('dt', { text: 'Duration' }), el('dd', { text: rx.duration }),
        el('dt', { text: 'Instructions' }), el('dd', { text: rx.sig }),
        el('dt', { text: 'Signed by' }), el('dd', { text: rx.signed_by || '–' }),
        el('dt', { text: 'Signed on' }), el('dd', { text: rx.signed_at ? when(rx.signed_at) : '–' }),
        el('dt', { text: 'Prescription ID' }), el('dd', { text: rx.prescription_id })
      ));
    if (rx.safety_warnings?.length) card.append(el('details', { class: 'adv', style: 'margin-top:.6rem' }, el('summary', { text: 'Safety notes (' + rx.safety_warnings.length + ')' }), el('ul', { class: 'fine' }, rx.safety_warnings.map(w => el('li', { text: w })))));
    card.append(el('p', { class: 'fine', style: 'margin-top:.6rem', text: 'This prescription was reviewed and signed by your clinician. Do not adjust your medication based on this page alone — always follow your clinician\'s verbal instructions.' }));
    return card;
  });
  show('My prescriptions', heading('My prescriptions', d.count + ' signed prescription(s).'), ...items);
}

// ------------------------------------------------------------------ cases
async function cases() {
  const d = await api('/api/v1/patient/me/recommendations');
  badge(d.items.filter(i => !i.reviewed).length);
  if (!d.items.length) return show('My cases', heading('My cases'), el('div', { class: 'empty' }, el('h3', { text: 'Nothing here yet' }), el('p', { text: 'Each check-in you send appears here with its status.' }), el('a', { class: 'btn', href: '#/check-in', text: 'Start a check-in' })));
  const ol = el('ol', { class: 'timeline' }, d.items.map(i => el('li', { 'data-state': i.emergency ? 'emergency' : i.reviewed ? 'done' : 'waiting' },
    el('a', { class: 'case', href: '#/cases/' + encodeURIComponent(i.recommendation_id) },
      el('div', {}, el('strong', { text: 'Visit ' + i.state_version + ' ' }), statusTag(i.status), i.emergency ? tag('Emergency wording', 'bad') : null),
      el('div', { class: 'when', text: when(i.created_at) + ' | ' + i.n_items + ' point' + (i.n_items === 1 ? '' : 's') + ' to review' })))));
  show('My cases', heading('My cases', 'Newest first.'), ol);
}

// A flag or a proposed action, always shown with its "why" and its sources, never as a bare label.
function whyBox(item) {
  const label = item.text || item.summary, level = item.severity || item.urgency, why = item.why || item.rationale;
  const box = el('div', { class: 'action-card' + (level === 'urgent' || level === 'critical' ? ' urgent' : level === 'high' ? ' high' : '') },
    el('div', {}, tag(level, level === 'critical' || level === 'urgent' ? 'critical' : level), el('strong', { text: label })));
  if (why) box.append(el('div', { class: 'fine', style: 'margin-top:.3rem', text: 'Why: ' + why }));
  if (item.options?.length) box.append(el('ul', { class: 'fine', style: 'margin:.3rem 0 0' }, item.options.map(o => el('li', { text: o }))));
  if (item.evidence?.length) {
    const seen = new Set(); const list = item.evidence.filter(e => !seen.has(e.id) && seen.add(e.id));
    box.append(el('details', { class: 'adv', style: 'margin-top:.4rem' }, el('summary', { text: 'Where this comes from (' + list.length + ')' }),
      list.map(e => el('div', { class: 'evbox' }, el('strong', { text: e.title }), el('div', { class: 'fine', text: (e.source || '') + (e.publication_date ? ', ' + e.publication_date : '') + (e.verbatim ? '' : ' (paraphrase)') }),
        el('p', { style: 'margin:.3rem 0' }, e.snippet), e.url ? el('a', { href: e.url, target: '_blank', rel: 'noopener noreferrer', text: 'Open original source' }) : null))));
  } else {
    box.append(el('p', { class: 'fine', style: 'margin:.3rem 0 0', text: 'Based on the record you entered, not a cited source.' }));
  }
  return box;
}

function caseBody(r) {
  const out = [];
  if (r.emergency) out.push(el('div', { class: 'emergency-box', role: 'alert' }, el('strong', { text: 'Possible emergency. ' }), r.emergency_text, el('ul', { style: 'margin:.5rem 0 0' }, r.red_flags.map(x => el('li', { text: x })))));
  out.push(el('div', { id: 'decision' }));
  if (r.deferred && !r.emergency) out.push(notice('warn', el('strong', { text: 'The system was not confident enough to suggest actions. ' }), r.abstention_reason || 'A clinician will take over.'));
  out.push(el('section', {}, el('h2', { text: 'Points your clinician will look at' }),
    r.items_for_clinician.length ? el('div', {}, r.items_for_clinician.map(whyBox)) : (r.deferred ? el('p', { class: 'muted', text: 'None listed.' }) : notice('ok', 'The rule set found no risk points for this check-in.'))));
  if (r.action_details.length) out.push(el('section', {}, el('h2', { text: 'Suggestions from the system' }), el('p', { class: 'muted', text: 'These are proposals for a clinician, not instructions. Do not change any medicine because of this page.' }), el('div', {}, r.action_details.map(whyBox))));
  if (r.what_changed.length) out.push(el('section', {}, el('h2', { text: 'What changed since last time' }), el('ul', { class: 'result-list' }, r.what_changed.map(a => el('li', {}, r.state_version > 1 ? [el('span', { class: 'changed', text: 'Changed' }), ' '] : null, a)))));
  out.push(el('details', { class: 'adv' }, el('summary', { text: 'How this was produced' }), el('dl', { class: 'kv', style: 'margin-top:.6rem' },
    el('dt', { text: 'Mode' }), el('dd', { text: words(r.mode) }), el('dt', { text: 'Checks run' }), el('dd', { text: r.agent_invocations + ' (reasoning: ' + r.reasoning_invocations + ')' }),
    el('dt', { text: 'Earlier reasoning reused' }), el('dd', { text: r.carried_forward ? 'Yes, nothing significant changed' : 'No' }), el('dt', { text: 'Confidence' }), el('dd', { text: fmt(r.aggregate_confidence) }),
    el('dt', { text: 'Independent validation' }), el('dd', { text: r.validation_passed ? 'Passed' : 'Raised findings (your clinician sees them)' }), el('dt', { text: 'Evidence passages cited' }), el('dd', { text: String(r.n_evidence) }),
    el('dt', { text: 'Claims withheld' }), el('dd', { text: String(r.n_flagged_claims) }))));
  out.push(el('p', { class: 'fine', text: r.disclaimer }));
  return out;
}
function paintDecision(r) {
  const box = $('#decision'); if (!box) return;
  clear(box);
  if (!r.reviewed) return box.append(notice('warn', el('strong', { text: 'Waiting for a clinician. ' }), 'Nothing below is final. This page updates by itself when a decision is recorded.'));
  const kind = r.status === 'clinician_approved' || r.status === 'clinician_modified' ? 'ok' : r.status === 'rejected' ? 'bad' : 'info';
  box.append(notice(kind, el('p', {}, el('strong', { text: (STATUS[r.status]?.[0] || words(r.status)) + '. ' }), 'Reviewed by ' + r.reviewed_by + (r.reviewed_at ? ' on ' + when(r.reviewed_at) : '') + '.'),
    r.clinician_notes ? el('p', {}, el('strong', { text: 'Their note: ' }), r.clinician_notes) : null));
}
async function caseDetail(id) {
  const r = await api('/api/v1/patient/status/' + encodeURIComponent(id));
  const f = flash; flash = null;
  show('Case, visit ' + r.state_version, el('p', {}, el('a', { href: '#/cases', text: 'All cases' })), heading('Visit ' + r.state_version, when(r.created_at) + '. Case ' + r.recommendation_id), f ? notice('ok', f) : null, ...caseBody(r));
  paintDecision(r);
  if (!r.reviewed) {
    poll = setInterval(async () => {
      if (document.hidden) return;
      try { const v = await api('/api/v1/patient/status/' + encodeURIComponent(id)); if (v.reviewed) { paintDecision(v); stopPoll(); badge(Math.max(0, +($('#waiting-badge').textContent || 1) - 1)); } } catch { /* keep trying */ }
    }, 5000);
  }
}

// ------------------------------------------------------------------ account
function account() {
  const dl = el('dl', { class: 'kv' }, el('dt', { text: 'Name' }), el('dd', { text: user.display_name }), el('dt', { text: 'Email' }), el('dd', { text: user.email }),
    el('dt', { text: 'Patient ID' }), el('dd', { text: user.patient_id }), el('dt', { text: 'Date of birth' }), el('dd', { text: user.birth_date }), el('dt', { text: 'Gender' }), el('dd', { text: user.gender }),
    el('dt', { text: 'Member since' }), el('dd', { text: day(user.created_at) }));
  const f = el('form', { novalidate: true }, el('div', { id: 'pw-msg', tabindex: '-1' }),
    el('div', { class: 'field' }, el('label', { for: 'current_password' }, 'Current password'), el('input', { id: 'current_password', name: 'current_password', type: 'password', autocomplete: 'current-password' })),
    el('div', { class: 'field' }, el('label', { for: 'new_password' }, 'New password', el('span', { class: 'hint', text: 'At least 10 characters.' })), el('input', { id: 'new_password', name: 'new_password', type: 'password', autocomplete: 'new-password' })),
    el('button', { class: 'btn', type: 'submit', text: 'Change password' }));
  f.addEventListener('submit', async (e) => {
    e.preventDefault(); clearErrors(f);
    const box = $('#pw-msg');
    if (!f.elements.current_password.value) return showError(f, 'current_password', 'Enter your current password.');
    if (!f.elements.new_password.value) return showError(f, 'new_password', 'Choose a new password.');
    await submitting(f.querySelector('button'), async () => {
      try { const r = await api('/api/auth/change-password', { method: 'POST', body: { current_password: f.elements.current_password.value, new_password: f.elements.new_password.value } }); f.reset(); clear(box).append(notice('ok', r.message)); box.focus(); }
      catch (err) { if (!(err.field && showError(f, err.field, err.message))) { clear(box).append(failed(err)); box.focus(); } }
    });
  });
  show('Account', heading('Account'), el('div', { class: 'form-sec' }, el('h2', { text: 'Your details' }), dl,
    el('p', { class: 'fine', style: 'margin-top:1rem', text: 'Only your patient ID, never your email address, is used in the analysis and audit records. To correct a detail or remove your account, use the contact form.' })),
    el('div', { class: 'form-sec' }, el('h2', { text: 'Change password' }), f),
    el('div', { class: 'btn-row' }, el('button', { class: 'btn btn-quiet', type: 'button', text: 'Sign out', onclick: signOut }), el('a', { href: '/contact', text: 'Contact us' })));
}

// ------------------------------------------------------------------ start
(async () => {
  const d = await me(true);
  if (!d.authenticated || d.user.role !== 'patient') return location.assign('/login?next=/portal');
  user = d.user;
  $('#who-name').textContent = user.display_name; $('#who-id').textContent = user.patient_id;
  api('/api/v1/patient/me/recommendations').then(h => badge(h.items.filter(i => !i.reviewed).length)).catch(() => {});
  route();
})();
