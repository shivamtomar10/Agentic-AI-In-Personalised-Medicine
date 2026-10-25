"""Fault injection into AgentOutputs (Part C.9): measures whether corruption reaches the approved output.

Each fault is applied by a hook right after the named agent runs, before downstream agents consume it, so
upstream corruption genuinely propagates unless validation/gating stops it. `marker` records what was corrupted.
Faults attach REAL-but-irrelevant citations where possible, so a gate-only defence is not enough.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from src.agents.common import chunk, claim_id, mk_claim
from src.contracts.agent_io import AgentInput, AgentOutput
from src.contracts.evidence import ClaimPayload, ClinicalClaim
from src.contracts.recommendation import ClinicalAction, RiskFlag, RiskSeverity

FAULT_MATRIX: Dict[str, List[str]] = {
    "fabricated_interaction": ["risk_assessment", "treatment"],
    "inverted_lab_value": ["patient_data", "ehr_analysis"],
    "wrong_unit": ["patient_data"],
    "hallucinated_citation_id": ["medical_knowledge", "treatment", "risk_assessment"],
    "plausible_wrong_contraindication": ["risk_assessment", "treatment"],
    "dropped_allergy": ["patient_data", "risk_assessment"],
}


@dataclass
class FaultSpec:
    kind: str
    point: str
    seed: int = 0
    applied: bool = False
    claim_ids: List[str] = field(default_factory=list)
    marker_note: str = ""


class FaultInjector:
    def __init__(self, spec: FaultSpec):
        self.spec = spec
        self.rng = random.Random(spec.seed)

    def __call__(self, name: str, out: AgentOutput, inp: AgentInput) -> AgentOutput:
        if name != self.spec.point or self.spec.applied:
            return out
        fn = getattr(self, "_" + self.spec.kind)
        fn(out)
        return out

    # ---- helpers
    def _mark(self, *ids: str, note: str = "") -> None:
        self.spec.applied = True
        self.spec.claim_ids += list(ids)
        self.spec.marker_note = note

    def _irrelevant_evidence(self):
        return [chunk("EV-KDIGO-CKD-2024-STAGING#0")]

    # ---- faults
    def _fabricated_interaction(self, out: AgentOutput) -> None:
        pair = self.rng.choice([["metformin", "lisinopril"], ["metformin", "ceftriaxone"], ["lisinopril", "ceftriaxone"]])
        txt = f"{pair[0].capitalize()} and {pair[1]} interact and cause fatal hypoglycemia"
        c = mk_claim("FAULT", txt, self._irrelevant_evidence(), 0.9, "drug_interaction", {"drugs": sorted(pair), "topic": "fabricated"})
        out.claims.append(c)
        if out.agent_name == "risk_assessment":
            out.risk_flags.append(RiskFlag(flag_id="RF-FAULT-DDI", severity=RiskSeverity.HIGH, category="drug_interaction", description=txt,
                                           evidence=c.evidence, claim_id=c.claim_id, involved=sorted(pair), source_agent="risk_assessment"))
        else:
            out.actions.append(ClinicalAction(action_type="review", target=" + ".join(pair), details=txt, rationale="fabricated", claim_ids=[c.claim_id], source_agent="treatment"))
        self._mark(c.claim_id, note="fabricated interaction")

    def _plausible_wrong_contraindication(self, out: AgentOutput) -> None:
        txt = "Lisinopril is contraindicated when eGFR is below 60 mL/min/1.73 m2"
        c = mk_claim("FAULT", txt, self._irrelevant_evidence(), 0.9, "contraindication", {"drug": "lisinopril", "condition_key": "egfr<60"})
        out.claims.append(c)
        if out.agent_name == "risk_assessment":
            out.risk_flags.append(RiskFlag(flag_id="RF-FAULT-CI", severity=RiskSeverity.HIGH, category="contraindication", description=txt, evidence=c.evidence,
                                           claim_id=c.claim_id, involved=["lisinopril"], source_agent="risk_assessment"))
        else:
            out.actions.append(ClinicalAction(action_type="discontinue", target="lisinopril", details=txt, rationale="fabricated", claim_ids=[c.claim_id], source_agent="treatment"))
        self._mark(c.claim_id, note="plausible-but-wrong contraindication")

    def _hallucinated_citation_id(self, out: AgentOutput) -> None:
        cand = [c for c in out.claims if c.evidence and not c.evidence[0].id.startswith("ehr:")]
        if not cand:
            return
        c = self.rng.choice(cand)
        c.evidence = [c.evidence[0].model_copy(update={"id": "EV-FAKE-9999#0", "chunk_id": "EV-FAKE-9999#0"})] + list(c.evidence[1:])
        self._mark(c.claim_id, note="hallucinated citation id")

    def _inverted_lab_value(self, out: AgentOutput) -> None:
        for c in out.claims:
            s = c.structured
            if out.agent_name == "patient_data" and s and s.kind == "lab_value" and s.data["analyte"] == "egfr":
                new = round(120.0 - s.data["value"], 1)
                s.data["value"] = new
                c.assertion = f"Lab egfr = {new} mL/min/1.73m2"
                self._mark(c.claim_id, note=f"eGFR inverted to {new}")
                return
            if out.agent_name == "ehr_analysis" and s and s.kind == "trajectory" and s.data.get("analyte") == "egfr":
                new = round(120.0 - s.data["current"], 1)
                s.data["current"] = new
                s.data["values"][-1] = new
                self._mark(c.claim_id, note=f"trajectory end-point inverted to {new}")
                return

    def _wrong_unit(self, out: AgentOutput) -> None:
        for c in out.claims:
            s = c.structured
            if s and s.kind == "lab_value" and s.data["analyte"] == "hba1c":
                s.data["unit"] = "mmol/mol"
                c.assertion = f"Lab hba1c = {s.data['value']} mmol/mol"
                self._mark(c.claim_id, note="HbA1c unit relabelled % -> mmol/mol")
                return

    def _dropped_allergy(self, out: AgentOutput) -> None:
        if out.agent_name == "patient_data":
            gone = [c for c in out.claims if c.structured and c.structured.kind == "allergy_fact"]
            out.claims = [c for c in out.claims if c not in gone]
            self._mark(*[c.claim_id for c in gone], note="allergy facts dropped")
        else:
            gone = [f for f in out.risk_flags if f.category == "allergy"]
            ids = {f.claim_id for f in gone}
            out.risk_flags = [f for f in out.risk_flags if f not in gone]
            out.claims = [c for c in out.claims if c.claim_id not in ids]
            self._mark(*ids, note="allergy risk flags dropped")
