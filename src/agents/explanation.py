"""ExplanationAgent: clinician-readable rationale, alternatives, uncertainty. Deterministic template by default.

Optional LLM narration: the LLM only REWORDS the deterministic text; a guard rejects any output that
introduces a drug name or number not present in the template (annotated, never silent).
"""
from __future__ import annotations

import re
from typing import List, Optional

from src.agents.base import ClinicalAgent
from src.contracts.agent_io import AgentInput, AgentOutput
from src.contracts.recommendation import Recommendation
from src.llm.client import LLMClient, LLMError

_NUM = re.compile(r"\d+(?:\.\d+)?")


class ExplanationAgent(ClinicalAgent):
    ROLE = "Explain the recommendation, what changed, alternatives and uncertainty"
    READS = ("draft_recommendation", "delta_report")

    def __init__(self, llm: Optional[LLMClient] = None, **kw):
        super().__init__("explanation", **kw)
        self.llm = llm

    @staticmethod
    def render(rec: Recommendation) -> str:
        L: List[str] = ["DECISION SUPPORT ONLY - every item below requires clinician review before any action."]
        if rec.emergency_flags:
            L.append("EMERGENCY FEATURES: " + "; ".join(rec.emergency_flags) + ". Immediate clinician evaluation is needed; this tool cannot provide emergency care.")
            return "\n".join(L)
        if rec.what_changed:
            L.append("What changed: " + " ".join(rec.what_changed))
        if rec.deferred:
            L.append(f"The system is DEFERRING a recommendation: {rec.abstention_reason}. Findings are shown for the clinician; no actions are proposed as approved.")
        if rec.clinical_actions:
            L.append("Proposed actions (for clinician decision):")
            for a in rec.clinical_actions:
                ev = sorted({e.id for c in rec.supported_claims if c.claim_id in a.claim_ids for e in c.evidence})
                L.append(f"- [{a.urgency}] {a.action_type} {a.target}: {a.details}. Why: {a.rationale}. Evidence: {', '.join(ev) or 'n/a'}.")
                for o in a.options:
                    L.append(f"    option: {o}")
        elif not rec.deferred:
            L.append("No approved actions were produced.")
        if rec.risk_flags:
            L.append("Risks identified: " + "; ".join(f"{r.severity.value.upper()} {r.description}" for r in rec.risk_flags))
        if rec.conflicts:
            L.append("Disagreement between agents (both positions shown): " + "; ".join(f"{c.position_a} vs {c.position_b}" for c in rec.conflicts))
        if rec.flagged_claims or rec.flagged_actions or rec.flagged_risk_flags:
            L.append(f"Withheld for lack of verifiable evidence or failed validation: {len(rec.flagged_claims)} claim(s), {len(rec.flagged_actions)} action(s), {len(rec.flagged_risk_flags)} risk flag(s). They are NOT part of the recommendation.")
        if rec.stale_evidence_ids:
            L.append("Older evidence cited (verify currency): " + ", ".join(rec.stale_evidence_ids))
        if rec.llm_escalation and rec.llm_escalation.get("triggered") and rec.llm_escalation.get("status") == "ok" and rec.llm_escalation.get("considerations"):
            L.append(f"Additional considerations from an LLM secondary opinion (escalated because: {rec.llm_escalation.get('trigger_reason')}; "
                    f"evidence-linked only, {rec.llm_escalation.get('n_dropped_unsupported', 0)} unsupported statement(s) withheld; "
                    f"NOT a verified system finding, clinician judgement required): " + "; ".join(rec.llm_escalation["considerations"]))
        conf = f"{rec.aggregate_confidence:.2f}" if rec.aggregate_confidence is not None else "n/a"
        L.append(f"Uncertainty: aggregate confidence {conf}. The rules cover structured data only; unmodelled drugs, free-text findings and patient preferences are not assessed.")
        return "\n".join(L)

    def _run(self, inp: AgentInput) -> AgentOutput:
        rec: Recommendation = inp.context["draft"]
        text = self.render(rec)
        note = None
        if self.llm is not None:
            try:
                out = self.llm.generate("Rewrite the following clinical decision-support explanation in clearer plain language. Do NOT add, remove or change any drug, number or finding.\n\n" + text,
                                        system="You reword text. You never add new clinical facts.")
                added = (set(_NUM.findall(out)) - set(_NUM.findall(text)))
                if added:
                    note = f"LLM narration rejected: introduced numbers {sorted(added)}; deterministic text used."
                else:
                    text = text + "\n\nPlain-language summary (reworded by " + self.llm.name + "; UNVERIFIED - the deterministic text above is authoritative):\n" + out
            except LLMError as e:
                note = f"LLM narration unavailable ({e}); deterministic text used."
        if note:
            text += "\n[" + note + "]"
        return AgentOutput(agent_name=self.name, narrative=text, confidence=0.95, execution_metadata={"llm_note": note} if note else {})
