import { el, clear, $, $$, tag, fmt, words, when, day, notice, table } from './dom.js';
import { api, me, ApiError } from './api.js';
import { clearErrors, showError, submitting } from './forms.js';
import { signOut } from './site.js';

const view = $('#view');
let user = null, sysStatus = null;
const S = { filter: 'awaiting', sel: null, rec: null, items: [] };

const STATUS = { requires_clinician_review: ['Awaiting review', 'warn'], clinician_approved: ['Approved', 'ok'], clinician_modified: ['Approved with changes', 'ok'], rejected: ['Rejected', 'bad'], escalated: ['Escalated, acknowledged', 'info'] };
const statusTag = (s) => { const [t, k] = STATUS[s] || [words(s), '']; return tag(t, k); };
const sev = (s) => tag(s, s);
const heading = (text, sub) => el('header', {}, el('h1', { id: 'page-h', tabindex: '-1', text }), sub ? el('p', { class: 'muted', text: sub }) : null);
const failed = (e) => notice('bad', e.message || String(e));
function show(title, ...kids) {
  clear(view).append(...kids);
  document.title = title + ' | Clinician console';
  $('#page-h')?.focus({ preventScroll: true });
}

// ------------------------------------------------------------------ router
async function route() {
  const [a, b] = location.hash.replace(/^#\/?/, '').split('/');
  $$('.side nav a[data-route]').forEach(x => (x.dataset.route === (a || '') ? x.setAttribute('aria-current', 'page') : x.removeAttribute('aria-current')));
  try {
    if (a === 'patients') return await (b ? patientDetail(decodeURIComponent(b)) : patients());
    if (a === 'account') return account();
    if (a === 'rx-audit') return await prescriptionAudit();
    return await queue(a === 'queue' && b ? decodeURIComponent(b) : null);
  } catch (e) {
    if (e instanceof ApiError && e.status === 401) return location.assign('/login?role=clinician&next=/clinician');
    show('Something went wrong', heading('Something went wrong'), failed(e));
  }
}
window.addEventListener('hashchange', route);

// ------------------------------------------------------------------ queue
const FILTERS = [['awaiting', 'Awaiting review', (i) => !i.reviewed_by], ['decided', 'Decided', (i) => !!i.reviewed_by && !i.deferred], ['deferred', 'Deferred or escalated', (i) => i.deferred], ['all', 'All', () => true]];
async function loadItems() {
  S.items = (await api('/api/v1/recommendations')).items;
  const n = S.items.filter(i => !i.reviewed_by).length;
  const b = $('#waiting-badge'); b.hidden = !n; b.textContent = String(n);
}
function statusBar() {
  const s = sysStatus; if (!s) return el('div');
  const llm = !s.llm.connected ? (s.llm.error ? 'misconfigured: ' + s.llm.error : 'off') : s.llm.provider + (s.llm.provider === 'ollama' ? ' (local)' : ' (cloud)');
  return el('div', { class: 'statusbar', role: 'status' }, el('span', { text: s.agents + ' agents' }), el('span', { text: s.evidence_chunks + ' evidence passages' }), el('span', {}, 'Audit chain ', tag(s.audit_chain_ok ? 'intact' : 'BROKEN', s.audit_chain_ok ? 'ok' : 'bad')),
    el('span', { text: 'Language model: ' + llm }), el('span', { text: s.persistent ? 'Records saved to disk' : 'Records in memory only' }));
}
async function queue(openId) {
  await loadItems();
  const list = el('div', { class: 'queue', role: 'list', 'aria-label': 'Cases' });
  const filters = el('div', { class: 'filters', role: 'group', 'aria-label': 'Filter cases' });
  const detail = el('section', { class: 'detail', 'aria-live': 'polite' }, el('div', { class: 'empty' }, el('h3', { text: 'Select a case' }), el('p', { text: 'Pick one from the list to see its evidence and record a decision.' })));
  const paintList = () => {
    clear(filters).append(...FILTERS.map(([k, label, fn]) => el('button', { type: 'button', 'aria-pressed': String(S.filter === k), onclick: () => { S.filter = k; paintList(); } }, label + ' ' + S.items.filter(fn).length)));
    const fn = FILTERS.find(f => f[0] === S.filter)[2];
    const items = S.items.filter(fn).sort((a, b) => Number(b.emergency && !b.reviewed_by) - Number(a.emergency && !a.reviewed_by));
    clear(list);
    if (!items.length) list.append(el('p', { class: 'muted', text: S.filter === 'awaiting' ? 'Queue is clear.' : 'Nothing here.' }));
    for (const r of items) list.append(el('button', { class: 'qitem', type: 'button', role: 'listitem', 'aria-current': String(r.recommendation_id === S.sel), onclick: () => { location.hash = '#/queue/' + encodeURIComponent(r.recommendation_id); } },
      el('div', {}, el('strong', { text: r.patient_id }), ' visit ' + r.state_version), el('div', { class: 'id', text: r.recommendation_id }),
      el('div', {}, statusTag(r.status), r.emergency ? tag('EMERGENCY', 'critical') : null, tag(r.n_actions + ' actions'), tag(r.agent_invocations + ' agents'))));
  };
  const head = [heading('Review queue', 'Cases waiting for a named clinician. Emergency cases are listed first.'), statusBar()];
  const tools = el('div', { class: 'btn-row', style: 'margin-bottom:.75rem' }, el('button', { class: 'btn btn-quiet btn-sm', type: 'button', text: 'Refresh', onclick: async () => { await loadItems(); paintList(); } }),
    sysStatus?.demo_mode ? el('button', { class: 'btn btn-quiet btn-sm', type: 'button', text: 'Add a demo patient', onclick: async (e) => {
      await submitting(e.target, async () => { try { const pid = 'DEMO-' + Date.now().toString(36).toUpperCase(); const d = await api('/api/v1/demo/seed?patient_id=' + pid, { method: 'POST' }); await loadItems(); location.hash = '#/queue/' + encodeURIComponent(d.recommendations.at(-1).recommendation_id); } catch (err) { list.before(failed(err)); } });
    } }) : null);
  show('Review queue', ...head, el('div', { class: 'console' }, el('div', {}, tools, filters, list), detail));
  S.sel = openId;
  paintList();
  if (openId) { S.rec = await api('/api/v1/recommendations/' + encodeURIComponent(openId)); const rec = S.items.find(i => i.recommendation_id === openId); if (rec) { S.filter = FILTERS.find(f => f[2](rec))?.[0] || 'all'; paintList(); } await renderDetail(detail, S.rec); }
}

// ------------------------------------------------------------------ case detail
function evidenceFor(rec, ids) { const m = new Map(); for (const c of rec.supported_claims) if (ids.includes(c.claim_id)) for (const e of c.evidence) m.set(e.id, e); return [...m.values()]; }
function showEvidence(e, box) {
  clear(box).append(el('div', { class: 'evbox' }, el('strong', { text: e.title }), el('div', { class: 'fine', text: e.source + (e.publication_date ? ', ' + e.publication_date : ', undated') + ' | id ' + e.id }), el('p', { text: e.snippet }),
    e.verbatim ? null : el('p', { class: 'fine', text: 'Curated paraphrase of the source. Verify against the original before relying on it.' }), e.url ? el('a', { href: e.url, target: '_blank', rel: 'noopener noreferrer', text: 'Open source' }) : null));
}
async function renderDetail(d, rec) {
  const tl = await api('/api/v1/patients/' + encodeURIComponent(rec.patient_id) + '/timeline');
  const evbox = el('div');
  const card = (title, ...kids) => el('div', { class: 'card' }, el('h2', { text: title }), ...kids);
  const m = rec.metrics, dr = rec.delta_report;
  const parts = [];

  const head = el('div', { class: 'card' }, el('div', { class: 'btn-row' }, el('h2', { style: 'flex:1;font-family:var(--serif);font-size:1.4rem;font-weight:560', text: rec.patient_id + ', record v' + rec.state_version }), statusTag(rec.status), tag('mode: ' + words(rec.execution_mode)), tag('confidence ' + fmt(rec.aggregate_confidence))),
    el('div', { class: 'fine', text: rec.recommendation_id + ' | created ' + when(rec.created_at) }));
  if (rec.emergency_flags.length) head.append(el('div', { class: 'emergency-box', role: 'alert' }, el('strong', { text: 'EMERGENCY FLAGS: ' }), rec.emergency_flags.join('; ')));
  if (rec.deferred) head.append(notice('warn', el('strong', { text: 'Deferred or escalated: ' }), rec.abstention_reason || ''));
  if (rec.what_changed.length) head.append(el('h3', { style: 'font-size:1rem;font-family:var(--sans);margin-top:1rem', text: 'What changed' }), el('ul', {}, rec.what_changed.map(x => el('li', { text: x }))));
  parts.push(head);

  // actions
  const ruleActions = rec.clinical_actions.filter(a => (a.source_type || 'rule') !== 'llm');
  const llmActions = rec.clinical_actions.filter(a => (a.source_type || 'rule') === 'llm');

  const ac = card('Proposed actions for your decision');
  if (!ruleActions.length) ac.append(el('p', { class: 'muted', text: rec.deferred ? 'None: the system deferred.' : 'None.' }));
  for (const a of ruleActions) ac.append(el('div', { class: 'action-card ' + a.urgency }, el('div', {}, tag(a.urgency, a.urgency === 'urgent' ? 'critical' : a.urgency === 'high' ? 'warn' : ''), tag('rule', 'info'), el('strong', { text: ' ' + a.action_type + ' ' + a.target })),
    el('div', { text: a.details }), el('div', { class: 'fine', text: 'Why: ' + a.rationale }), a.options.length ? el('ul', { class: 'fine' }, a.options.map(o => el('li', { text: o }))) : null,
    el('div', {}, 'Evidence: ', evidenceFor(rec, a.claim_ids).map(e => el('button', { class: 'btn btn-quiet btn-sm', style: 'margin:.15rem .3rem .15rem 0;min-height:1.9rem', type: 'button', title: e.title, onclick: () => showEvidence(e, evbox), text: e.id.startsWith('ehr:') ? 'patient record' : e.id })))));
  ac.append(evbox);
  parts.push(ac);

  if (llmActions.length) {
    const llmAc = card('LLM-suggested items (requires clinician tick before approval)');
    for (const a of llmActions) llmAc.append(el('div', { class: 'action-card llm-card', style: 'border-left:4px solid #3b82f6;margin-bottom:.8rem;padding:.8rem;' },
      el('div', { style: 'display:flex;align-items:center;gap:.5rem;' },
        el('input', { type: 'checkbox', class: 'llm-tickbox', 'data-target': a.target }),
        tag('LLM-suggested', 'warn'),
        el('strong', { text: a.action_type + ' ' + a.target })
      ),
      el('div', { style: 'margin-top:.4rem;', text: a.details }),
      el('div', { class: 'fine', text: 'Why: ' + a.rationale }),
      el('div', {}, 'Evidence: ', evidenceFor(rec, a.claim_ids).map(e => el('button', { class: 'btn btn-quiet btn-sm', style: 'margin:.15rem .3rem .15rem 0;min-height:1.9rem', type: 'button', title: e.title, onclick: () => showEvidence(e, evbox), text: e.id.startsWith('ehr:') ? 'patient record' : e.id })))
    ));
    parts.push(llmAc);
  }

  // risks
  const rk = card('Risk flags');
  rk.append(rec.risk_flags.length ? table(['Severity', 'Flag', 'Description', 'Advice'], rec.risk_flags.map(f => [sev(f.severity), f.flag_id, f.description, f.mitigation_advice || ''])) : el('p', { class: 'muted', text: 'None.' }));
  if (rec.conflicts.length) rk.append(el('h3', { style: 'font-size:1rem;font-family:var(--sans);margin-top:1rem', text: 'Agents disagree (both positions shown)' }), table(['Position A', 'Position B'], rec.conflicts.map(c => [c.position_a, c.position_b])));
  parts.push(rk);

  // how computed
  const tr = card('How it was computed', el('p', { class: 'fine', text: `Invocations: reasoning ${m.reasoning_invocations}, context ${m.context_invocations}, safety ${m.safety_invocations}, total ${m.agent_invocations}. Wall clock ${fmt(m.wall_clock_ms, 1)} ms. Safety re-plans: ${m.safety_replans}.` }),
    table(['Agent', 'Status', 'Why', 'Claims', 'ms'], rec.trace.map(t => [t.agent, tag(words(t.status), t.status === 'ran' ? 'ok' : t.status === 'abstained' ? 'warn' : 'info'), t.reason, String(t.n_claims), fmt(t.latency_ms, 1)])));
  if (dr && dr.deltas.length) tr.append(el('h3', { style: 'font-size:1rem;font-family:var(--sans);margin-top:1rem', text: 'Detected changes (rule-based, auditable)' }), table(['Category', 'Field', 'Old', 'New', 'Significance', 'Rule'], dr.deltas.map(x => [x.category, x.field_path, String(x.old_value ?? ''), String(x.new_value ?? ''), fmt(x.significance), x.rule_id + (x.classifier === 'default' ? ' (fail-safe default)' : '')])));
  const other = rec.execution_mode === 'selective_replan' ? 'full_rerun' : 'selective_replan', cmp = el('div', { class: 'fine', role: 'status' });
  tr.append(el('div', { class: 'btn-row', style: 'margin-top:1rem' }, el('button', { class: 'btn btn-quiet btn-sm', type: 'button', text: 'Re-run latest record as ' + words(other), onclick: async () => {
    try { const r = await api('/api/v1/patients/' + encodeURIComponent(rec.patient_id) + '/rerun?mode=' + other, { method: 'POST' }); cmp.textContent = `Re-run (${words(other)}): ${r.agent_invocations} agents (${r.reasoning_invocations} reasoning), ${r.n_actions} actions, ${r.n_risk_flags} risk flags. This run: ${m.agent_invocations} agents, ${rec.clinical_actions.length} actions, ${rec.risk_flags.length} flags.`; await loadItems(); }
    catch (e) { cmp.textContent = e.message; } } }), cmp));
  parts.push(tr);

  // record
  parts.push(card('Longitudinal record (hash-chained versions)', el('p', { class: 'fine', text: 'Chain verified: ' + tl.chain_verified }),
    table(['Ver', 'Date', 'Labs', 'Vitals', 'Medications', 'Allergies', 'Hash'], tl.versions.map(v => [String(v.version), day(v.timestamp), Object.entries(v.labs).map(([k, x]) => k + ' ' + fmt(x[0]) + ' ' + x[1]).join('; '), Object.entries(v.vitals).map(([k, x]) => k + ' ' + fmt(x[0])).join('; '), v.medications.join(', '), v.allergies.join(', '), v.state_hash.slice(0, 10)]))));

  // validation
  const vr = rec.validation_report, nW = rec.flagged_claims.length + rec.flagged_actions.length + rec.flagged_risk_flags.length;
  const wh = card('Validation and withheld items', el('p', { text: vr ? (vr.passed ? `Validation passed (${vr.checks_run} checks).` : `Validation raised ${vr.findings.length} finding(s) over ${vr.checks_run} checks.`) : 'Validation disabled.' }));
  if (vr && vr.findings.length) wh.append(table(['Check', 'Severity', 'Agent', 'Detail'], vr.findings.map(f => [f.check, f.severity, f.agent || '', f.detail])));
  wh.append(el('h3', { style: 'font-size:1rem;font-family:var(--sans);margin-top:1rem', text: `Withheld from the approved output (${nW})` }));
  wh.append(nW ? el('ul', {}, [...rec.flagged_claims.map(c => el('li', { text: 'claim: ' + c.assertion + ' [' + c.status + ']' })), ...rec.flagged_actions.map(a => el('li', { text: 'action: ' + a.action_type + ' ' + a.target })), ...rec.flagged_risk_flags.map(f => el('li', { text: 'risk flag: ' + f.description }))]) : el('p', { class: 'muted', text: 'Nothing withheld.' }));
  parts.push(wh);
  parts.push(card('Explanation', el('pre', { class: 'expl', text: rec.explanation })));
  if (rec.llm_escalation && rec.llm_escalation.triggered) {
    const e = rec.llm_escalation;
    const box = card('Language-model second opinion (advisory, unverified)', el('p', { class: 'fine', text: `Escalated because: ${e.trigger_reason}. Status: ${e.status}${e.detail ? ' (' + e.detail + ')' : ''}. It cannot change actions, risk flags or confidence.` }));
    if (e.status === 'ok') box.append(e.considerations.length ? el('ul', {}, e.considerations.map(x => el('li', { text: x }))) : el('p', { class: 'muted', text: `No statement survived the evidence check (${e.n_dropped_unsupported} dropped).` }));
    parts.push(box);
  }
  parts.push(reviewCard(rec));
  parts.push(await prescriptionsCard(rec));
  clear(d).append(...parts);
}

function reviewCard(rec) {
  const rv = card2('Your decision');
  if (rec.reviewed_by) {
    rv.append(notice('ok', el('strong', { text: `${STATUS[rec.status]?.[0] || words(rec.status)} by ${rec.reviewed_by}` }), el('div', { text: (rec.reviewed_at ? when(rec.reviewed_at) + '. ' : '') + 'Notes: ' + (rec.review_notes || '') })));
    return rv;
  }
  const opts = rec.deferred ? [['acknowledge', 'Acknowledge the escalation'], ['reject', 'Reject']] : [['approve', 'Approve'], ['modify', 'Approve with modifications'], ['reject', 'Reject']];
  const f = el('form', { novalidate: true }, el('div', { id: 'rv-msg', tabindex: '-1' }),
    el('p', { class: 'fine', text: 'You are signing as ' + user.display_name + ' (' + user.staff_id + '). The decision and your rationale are written to the audit log and shown to the patient.' }),
    el('div', { class: 'field' }, el('label', { for: 'action' }, 'Decision'), el('select', { id: 'action', name: 'action' }, opts.map(([v, t]) => el('option', { value: v, text: t })))),
    el('div', { class: 'field' }, el('label', { for: 'rationale' }, 'Written rationale', el('span', { class: 'hint', text: 'Required. At least 5 characters.' })), el('textarea', { id: 'rationale', name: 'rationale', rows: 4, maxlength: 2000 })),
    el('div', { class: 'field', id: 'mod-field', hidden: true }, el('label', { for: 'modifications' }, 'Modifications', el('span', { class: 'hint', text: 'Required when approving with modifications.' })), el('textarea', { id: 'modifications', name: 'modifications', rows: 3, maxlength: 2000 })),
    el('button', { class: 'btn', type: 'submit', text: 'Record decision' }));
  f.elements.action.addEventListener('change', () => { $('#mod-field', f).hidden = f.elements.action.value !== 'modify'; });
  f.addEventListener('submit', async (e) => {
    e.preventDefault(); clearErrors(f);
    const unticked = $$('.llm-tickbox', f.closest('.detail')).filter(cb => !cb.checked);
    if ((f.elements.action.value === 'approve' || f.elements.action.value === 'modify') && unticked.length > 0) {
      const box = $('#rv-msg', f); clear(box).append(failed(new Error('You must review and tick each LLM-suggested item before approving.'))); box.focus(); return;
    }
    if (f.elements.rationale.value.trim().length < 5) return showError(f, 'rationale', 'Write a rationale of at least 5 characters.');
    if (f.elements.action.value === 'modify' && !f.elements.modifications.value.trim()) return showError(f, 'modifications', 'Describe the modifications.');
    await submitting(f.querySelector('button[type=submit]'), async () => {
      try {
        await api('/api/v1/recommendations/' + encodeURIComponent(rec.recommendation_id) + '/review', { method: 'POST', body: { action: f.elements.action.value, rationale: f.elements.rationale.value, modifications: f.elements.modifications.value.trim() || null } });
        await loadItems(); const fresh = await api('/api/v1/recommendations/' + encodeURIComponent(rec.recommendation_id)); S.rec = fresh; await renderDetail(rv.closest('.detail'), fresh);
        $('.qitem[aria-current=true]')?.focus();
      } catch (err) { const box = $('#rv-msg', f); clear(box).append(failed(err)); box.focus(); }
    });
  });
  rv.append(f);
  return rv;
}
function card2(title) { return el('div', { class: 'card' }, el('h2', { text: title })); }

// ------------------------------------------------------------------ prescription signing
const RX_STATUS = {
  draft: ['Draft – awaiting clinician sign-off', 'warn'],
  signed: ['Signed', 'ok'],
  rejected: ['Rejected', 'bad'],
  cancelled: ['Cancelled', 'info'],
};
const rxTag = (s) => { const [t, k] = RX_STATUS[s] || [s, '']; return tag(t, k); };

async function prescriptionsCard(rec) {
  const box = card2('Draft prescriptions');
  const canSign = rec.status === 'clinician_approved' || rec.status === 'clinician_modified';
  const rxs = rec.draft_prescriptions || [];

  if (!rxs.length) {
    box.append(el('p', { class: 'muted', text: 'No draft prescriptions generated for this case.' }));
    return box;
  }

  box.append(el('p', { class: 'fine', text: canSign
    ? 'Review each prescription and sign, reject or cancel. Signing issues a tamper-evident digital signature and writes to the audit log.'
    : 'Approve or modify the recommendation above before you can sign prescriptions.' }));

  for (const rx of rxs) {
    const rxWrap = el('div', { class: 'action-card' + (rx.status === 'draft' ? ' high' : ''), style: 'margin-bottom:1.2rem' });
    rxWrap.append(
      el('div', {}, rxTag(rx.status), el('strong', { text: ' ' + rx.medication })),
      el('div', { class: 'fine', style: 'margin-top:.3rem' },
        `${rx.dosage} · ${rx.route} · ${rx.frequency} · ${rx.duration}`),
      el('div', { style: 'margin-top:.2rem', text: rx.sig }),
    );
    if (rx.safety_warnings?.length) {
      rxWrap.append(el('ul', { class: 'fine', style: 'color:var(--warn-fg,#7a4f00);margin:.3rem 0 0' },
        rx.safety_warnings.map(w => el('li', { text: '⚠ ' + w }))));
    }
    if (rx.status === 'signed') {
      rxWrap.append(el('div', { class: 'fine', style: 'margin-top:.4rem' },
        el('strong', { text: 'Signed by: ' }), rx.signed_by || '',
        el('br'), el('strong', { text: 'Digital signature: ' }),
        el('code', { text: rx.digital_signature || '' })));
    } else if (rx.status === 'draft' && canSign) {
      const msg = el('div', { style: 'margin-top:.4rem', tabindex: '-1' });
      const f = el('form', { novalidate: true, style: 'margin-top:.6rem' },
        el('div', { class: 'field' },
          el('label', { for: 'rx-notes-' + rx.prescription_id }, 'Note (optional)', el('span', { class: 'hint', text: 'Up to 1000 characters.' })),
          el('textarea', { id: 'rx-notes-' + rx.prescription_id, name: 'notes', rows: 2, maxlength: 1000 })),
        el('div', { class: 'btn-row', style: 'gap:.5rem;margin-top:.4rem' },
          el('button', { class: 'btn btn-sm', type: 'submit', 'data-action': 'sign', text: '✓ Sign prescription' }),
          el('button', { class: 'btn btn-quiet btn-sm', type: 'button', 'data-action': 'reject', text: 'Reject', onclick: (e) => submitRx(e, f, rx.prescription_id, rec.recommendation_id, 'reject', msg, rxWrap) }),
          el('button', { class: 'btn btn-quiet btn-sm', type: 'button', 'data-action': 'cancel', text: 'Cancel Rx', onclick: (e) => submitRx(e, f, rx.prescription_id, rec.recommendation_id, 'cancel', msg, rxWrap) })));
      f.addEventListener('submit', (e) => { e.preventDefault(); submitRx(e, f, rx.prescription_id, rec.recommendation_id, 'sign', msg, rxWrap); });
      rxWrap.append(f, msg);
    }
    box.append(rxWrap);
  }
  return box;
}

async function submitRx(e, f, rxId, recId, action, msgBox, wrapper) {
  e.preventDefault?.();
  const notes = f.elements.notes?.value?.trim() || null;
  await submitting(f.querySelector('button[data-action=' + action + ']'), async () => {
    try {
      const res = await api('/api/v1/recommendations/' + encodeURIComponent(recId) + '/prescriptions/' + encodeURIComponent(rxId) + '/sign',
        { method: 'POST', body: { action, notes } });
      const [label, kind] = RX_STATUS[res.status] || [res.status, 'info'];
      clear(msgBox).append(notice('ok', el('strong', { text: label + '. ' }), 'Signed by ' + res.signed_by + '. Signature: ', el('code', { text: res.digital_signature })));
      // update local badge
      const statusEl = wrapper.querySelector('.tag');
      if (statusEl) { statusEl.textContent = label; statusEl.className = 'tag ' + kind; }
      // hide the form
      f.style.display = 'none';
    } catch (err) { clear(msgBox).append(failed(err)); msgBox.focus(); }
  });
}


// ------------------------------------------------------------------ prescription audit trail
async function prescriptionAudit() {
  const d = await api('/api/v1/audit/prescriptions');
  show('Prescription audit trail',
    heading('Prescription audit trail', `${d.count} event(s). Audit chain: ${d.chain_ok ? 'intact ✓' : 'BROKEN ✗'}`),
    d.count
      ? table(
          ['Time', 'Patient', 'Prescription ID', 'Medication', 'Action', 'Clinician', 'Digital signature'],
          d.events.map(e => {
            const p = e.payload || {};
            return [when(e.timestamp), p.patient_id || '', p.prescription_id || '', p.medication || '', p.action || '', p.clinician_id || '', el('code', { style: 'font-size:.7rem', text: (p.digital_signature || '').slice(0, 20) + '…' })];
          })
        )
      : el('div', { class: 'empty' }, el('h3', { text: 'No prescription events yet' }), el('p', { text: 'Events appear here once a clinician signs, rejects or cancels a prescription.' })));
}

// ------------------------------------------------------------------ patients

async function patients() {
  const d = await api('/api/v1/patients');
  if (!d.patients.length) return show('Patients', heading('Patients'), el('div', { class: 'empty' }, el('h3', { text: 'No patients yet' }), el('p', { text: 'Records appear when patients send check-ins.' })));
  show('Patients', heading('Patients', d.patients.length + ' with recorded visits.'), table(['Patient ID', 'Visits', 'Awaiting review', 'Last activity', ''],
    d.patients.map(p => [el('strong', { text: p.patient_id }), String(p.versions), p.awaiting_review ? tag(String(p.awaiting_review), 'warn') : '0', day(p.last_activity), el('a', { href: '#/patients/' + encodeURIComponent(p.patient_id), text: 'Open record' })])));
}
async function patientDetail(pid) {
  const [tl, q] = await Promise.all([api('/api/v1/patients/' + encodeURIComponent(pid) + '/timeline'), api('/api/v1/recommendations')]);
  const recs = q.items.filter(i => i.patient_id === pid);
  show('Patient ' + pid, el('p', {}, el('a', { href: '#/patients', text: 'All patients' })), heading(pid, 'Record chain verified: ' + tl.chain_verified),
    el('h2', { text: 'Versions' }), table(['Ver', 'Date', 'Labs', 'Vitals', 'Medications', 'Allergies'], tl.versions.map(v => [String(v.version), day(v.timestamp), Object.entries(v.labs).map(([k, x]) => k + ' ' + fmt(x[0])).join('; '), Object.entries(v.vitals).map(([k, x]) => k + ' ' + fmt(x[0])).join('; '), v.medications.join(', '), v.allergies.join(', ')])),
    el('h2', { style: 'margin-top:2rem', text: 'Cases' }), table(['Visit', 'Status', 'Actions', 'Flags', ''], recs.map(r => [String(r.state_version), statusTag(r.status), String(r.n_actions), String(r.n_risk_flags), el('a', { href: '#/queue/' + encodeURIComponent(r.recommendation_id), text: 'Open' })])));
}

// ------------------------------------------------------------------ account
function account() {
  const f = el('form', { novalidate: true }, el('div', { id: 'pw-msg', tabindex: '-1' }),
    el('div', { class: 'field' }, el('label', { for: 'current_password' }, 'Current password'), el('input', { id: 'current_password', name: 'current_password', type: 'password', autocomplete: 'current-password' })),
    el('div', { class: 'field' }, el('label', { for: 'new_password' }, 'New password', el('span', { class: 'hint', text: 'At least 10 characters.' })), el('input', { id: 'new_password', name: 'new_password', type: 'password', autocomplete: 'new-password' })),
    el('button', { class: 'btn', type: 'submit', text: 'Change password' }));
  f.addEventListener('submit', async (e) => {
    e.preventDefault(); clearErrors(f); const box = $('#pw-msg');
    if (!f.elements.current_password.value) return showError(f, 'current_password', 'Enter your current password.');
    if (!f.elements.new_password.value) return showError(f, 'new_password', 'Choose a new password.');
    await submitting(f.querySelector('button'), async () => {
      try { const r = await api('/api/auth/change-password', { method: 'POST', body: { current_password: f.elements.current_password.value, new_password: f.elements.new_password.value } }); f.reset(); clear(box).append(notice('ok', r.message)); box.focus(); }
      catch (err) { if (!(err.field && showError(f, err.field, err.message))) { clear(box).append(failed(err)); box.focus(); } }
    });
  });
  show('Account', heading('Account'), el('div', { class: 'form-sec' }, el('h2', { text: 'Your details' }), el('dl', { class: 'kv' }, el('dt', { text: 'Name' }), el('dd', { text: user.display_name }), el('dt', { text: 'Email' }), el('dd', { text: user.email }), el('dt', { text: 'Staff ID' }), el('dd', { text: user.staff_id }), el('dt', { text: 'Member since' }), el('dd', { text: day(user.created_at) })),
    el('p', { class: 'fine', style: 'margin-top:1rem', text: 'Your staff ID is recorded with every decision and cannot be changed. For your protection, clinician sessions end after 30 minutes without activity.' })),
    el('div', { class: 'form-sec' }, el('h2', { text: 'Change password' }), f), el('button', { class: 'btn btn-quiet', type: 'button', text: 'Sign out', onclick: signOut }));
}

// ------------------------------------------------------------------ start
(async () => {
  const d = await me(true);
  if (!d.authenticated || d.user.role !== 'clinician') return location.assign('/login?role=clinician&next=/clinician');
  user = d.user;
  $('#who-name').textContent = user.display_name; $('#who-id').textContent = user.staff_id;
  try { sysStatus = await api('/api/v1/system/status'); } catch { /* status bar is optional */ }
  route();
})();
