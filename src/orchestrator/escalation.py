"""LLM escalation for hard cases (Part C.6 extension, added on explicit request).

WHAT THIS IS: when the rule-based pipeline's own confidence lands in an ambiguous band, or it found
conflicting risk flags/actions, or it had to drop some claims as unsupported, that is exactly the
situation a clinician would want a second opinion on. This module optionally asks a configured LLM
for additional clinical considerations to put in front of the clinician - NOT a replacement decision.

WHAT THIS IS NOT, on purpose (this is the whole point of the design):
  - It cannot add, remove or change a clinical_action or risk_flag. Those stay 100% rule-based.
  - It cannot change aggregate_confidence, calibrated_confidence or the abstain/proceed decision.
    The rule-based pipeline already decided whether to proceed BEFORE this module is even considered.
  - Every line it returns is split into atomic claims and run through the EvidenceLinker in STRICT mode
    (every content word must occur in a retrieved evidence sentence). Only claims that restate retrieved
    evidence survive; everything else is dropped and only the *count* is reported.
    KNOWN LIMIT (measured): the linker is lexical, not NLI. In non-strict mode a probe let through
    'cross-reactivity ... is high overall' and 'penicillin-allergic patients should routinely receive
    desensitisation'; strict mode blocks those (regression-tested) but a lexical check can still be fooled
    by rearranged words, so considerations are labelled advisory/unverified and a clinician must judge them.
    Practical consequence: the LLM step can select and restate evidence; it cannot add new clinical insight.
  - The prompt sent to the model is built ONLY from already-structured Facts (age band, conditions,
    meds, labs, existing flags) - never raw clinical notes - and still passes through LLMClient's PHI
    guard before it leaves the process, exactly like the ExplanationAgent's narration calls.
  - If the LLM is unavailable, errors, or is not configured at all, the run completes normally with
    llm_escalation={"triggered": False/... , "status": "unavailable"/"not_triggered"} - this feature can
    never be the reason a clinical recommendation fails to come back.

HONEST STATUS: unit-tested with ScriptedLLMClient only (mixed entailed/fabricated fixtures). Like the
rest of src/llm, it has not been exercised against a live model in the build environment - see
docs/limitations.md item 1 and scripts/llm_smoke.py, which now also reports escalation trigger rate.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

from src.agents.common import Facts
from src.contracts.evidence import ClinicalClaim, EvidenceRef
from src.llm.client import LLMClient, LLMError
from src.provenance.claim_extractor import ClaimExtractor, EvidenceLinker

_LINKER = EvidenceLinker(strict=True)   # LLM text may only RESTATE retrieved evidence; novel wording is dropped
_EXTRACTOR = ClaimExtractor()

SYSTEM_PROMPT = (
    "You are a secondary reviewer for a rule-based clinical decision-support system, consulted only "
    "because the rule engine found this case ambiguous. State general clinical knowledge as short, "
    "plain declarative sentences (one assertion per line, no bullets, no preamble). Do not invent "
    "specific lab values, dates or dosages beyond what is given. Do not address the patient directly. "
    "If you are not confident in a statement, omit it rather than hedge in-line."
)


@dataclass
class EscalationTrigger:
    should_escalate: bool
    reason: str


def check_trigger(aggregate_confidence: float, escalation_band: float, abstention_threshold: float,
                   n_conflicts: int, n_unsupported: int) -> EscalationTrigger:
    """Hard case = the rule engine proceeded (did not abstain) but only barely, OR it had to reconcile
    conflicting flags/actions, OR some claims were dropped as unsupported. Any one of these is reason
    enough; a comfortably-confident, conflict-free, fully-supported case is NOT escalated (saves cost,
    latency and unnecessary data exposure to the model - "escalation for difficult cases", not for all)."""
    if n_conflicts > 0:
        return EscalationTrigger(True, f"{n_conflicts} conflicting risk flag(s)/action(s) required reconciliation")
    if n_unsupported > 0:
        return EscalationTrigger(True, f"{n_unsupported} claim(s) were dropped as unsupported by the provenance gate")
    if abstention_threshold <= aggregate_confidence < abstention_threshold + escalation_band:
        return EscalationTrigger(True, f"aggregate confidence {aggregate_confidence:.2f} is in the ambiguous band just above the abstention threshold {abstention_threshold:.2f}")
    return EscalationTrigger(False, "confidence, conflicts and evidence coverage were all comfortably within normal range")


def _case_summary(f: Facts, existing_flag_ids: List[str]) -> str:
    """Structured, redacted case facts only - no patient identifiers, no free text notes."""
    age = f"{int(f.age // 10) * 10}s" if f.age is not None else "unknown age"
    lines = [
        f"Patient: {age}, {'pregnant' if f.pregnant else 'not pregnant'}.",
        f"Active conditions: {', '.join(f.conditions) or 'none recorded'}.",
        f"Current medications: {', '.join(f.meds) or 'none recorded'}.",
        f"Documented allergies: {', '.join(f.allergies) or 'none recorded'}.",
        f"Recent lab values: {', '.join(f'{k}={v}' for k, v in f.labs.items()) or 'none recorded'}.",
        f"Risk flags already identified by the rule engine: {', '.join(existing_flag_ids) or 'none'}.",
    ]
    return "\n".join(lines)


def escalate(llm: Optional[LLMClient], f: Facts, existing_flag_ids: List[str], trigger: EscalationTrigger,
             evidence_pool: List[EvidenceRef]) -> Dict:
    """Returns the llm_escalation dict for Recommendation. Never raises: a failure here degrades to
    status='unavailable', it never blocks or alters the rule-based recommendation being returned."""
    if not trigger.should_escalate:
        return {"triggered": False, "status": "not_triggered", "trigger_reason": trigger.reason}
    if llm is None:
        return {"triggered": True, "status": "unavailable", "trigger_reason": trigger.reason, "detail": "no LLM configured (LLM_PROVIDER=none)"}
    try:
        text = llm.generate(_case_summary(f, existing_flag_ids), system=SYSTEM_PROMPT)
    except LLMError as e:
        return {"triggered": True, "status": "unavailable", "trigger_reason": trigger.reason, "detail": str(e)}
    claims: List[ClinicalClaim] = _EXTRACTOR.extract(text, "llm_escalation")
    linked = _LINKER.link(claims, evidence_pool) if evidence_pool else [c.model_copy(update={"evidence": []}) for c in claims]
    supported = [c for c in linked if c.evidence]
    return {
        "triggered": True, "status": "ok", "trigger_reason": trigger.reason, "model": llm.name,
        "considerations": [c.assertion for c in supported],
        "n_proposed": len(claims), "n_dropped_unsupported": len(claims) - len(supported),
    }
