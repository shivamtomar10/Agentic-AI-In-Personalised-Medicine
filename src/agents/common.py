"""Shared helpers for agents: deterministic claim ids, EHR evidence refs, and the Facts view.

`Facts` is what downstream agents consume: it is rebuilt from the *PatientDataAgent's claims*, not
from raw state. That is deliberate: an upstream error becomes downstream ground truth, which is the
propagation failure mode measured by PCR (Part A.3 G3).
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

from src.contracts.agent_io import AgentInput
from src.contracts.evidence import ClaimPayload, ClinicalClaim, EvidenceRef, SourceType
from src.knowledge.drugs import get_drug_knowledge
from src.knowledge.evidence_store import get_evidence_store
from src.units import UnitError, analyte_key, to_canonical


class MissingUpstreamError(RuntimeError):
    pass


def claim_id(agent: str, assertion: str, payload: Optional[dict] = None) -> str:
    h = hashlib.sha1((agent + "|" + assertion + "|" + json.dumps(payload or {}, sort_keys=True, default=str)).encode()).hexdigest()
    return f"CLM-{h[:10]}"


def mk_claim(agent: str, assertion: str, evidence: List[EvidenceRef], confidence: float,
             kind: Optional[str] = None, data: Optional[dict] = None) -> ClinicalClaim:
    payload = ClaimPayload(kind=kind, data=data or {}) if kind else None
    return ClinicalClaim(claim_id=claim_id(agent, assertion, data), assertion=assertion, confidence=confidence,
                         evidence=evidence, agent_source=agent, structured=payload)


def ehr_ref(patient_id: str, version: int, field_path: str, snippet: str) -> EvidenceRef:
    return EvidenceRef(id=f"ehr:{patient_id}:v{version}:{field_path}", source=SourceType.LOCAL_EHR,
                       title=f"Patient record {patient_id} v{version}", snippet=snippet, verbatim=True)


def chunk(chunk_id: str) -> EvidenceRef:
    """Fetch one stable corpus chunk by id (fails loudly if missing - Rule 6)."""
    c = get_evidence_store().get(chunk_id)
    if c is None:
        raise KeyError(f"Evidence chunk {chunk_id} not in corpus")
    return c.ref


def stmt(chunk_id: str) -> str:
    """Assertion text = the evidence sentence itself (so entailment is exact)."""
    return chunk(chunk_id).snippet.rstrip(".")


def ckd_stage(egfr: float) -> str:
    return "G1" if egfr >= 90 else "G2" if egfr >= 60 else "G3a" if egfr >= 45 else "G3b" if egfr >= 30 else "G4" if egfr >= 15 else "G5"


@dataclass
class Facts:
    labs: Dict[str, float] = field(default_factory=dict)
    vitals: Dict[str, float] = field(default_factory=dict)
    allergies: List[str] = field(default_factory=list)
    meds: List[str] = field(default_factory=list)
    conditions: List[str] = field(default_factory=list)
    resolved_conditions: List[str] = field(default_factory=list)
    age: Optional[float] = None
    pregnant: bool = False
    unreadable: List[str] = field(default_factory=list)

    @classmethod
    def from_upstream(cls, inp: AgentInput) -> "Facts":
        pd = inp.upstream_outputs.get("patient_data")
        if pd is None:
            raise MissingUpstreamError(f"{inp.agent_name}: patient_data output is required")
        f = cls()
        for c in pd.claims:
            if not c.structured:
                continue
            k, d = c.structured.kind, c.structured.data
            try:
                if k == "lab_value":
                    f.labs[d["analyte"]] = to_canonical(d["analyte"], d["value"], d["unit"])
                elif k == "vital_value":
                    f.vitals[d["analyte"]] = to_canonical(d["analyte"], d["value"], d["unit"])
            except UnitError as e:
                f.unreadable.append(str(e))
            if k == "allergy_fact":
                f.allergies.append(d["substance"])
            elif k == "medication_fact":
                f.meds.append(d["generic"])
            elif k == "condition_fact":
                (f.conditions if d["status"] in ("active", "recurrence", "relapse") else f.resolved_conditions).append(d["name"])
            elif k == "demographic_fact":
                f.age, f.pregnant = d["age"], d["pregnant"]
        f.meds = sorted(set(f.meds))
        return f

    def has_condition(self, *kw: str) -> bool:
        return any(k in c.lower() for c in self.conditions for k in kw)

    def meds_in_class(self, cls_: str) -> List[str]:
        dk = get_drug_knowledge()
        return [m for m in self.meds if dk.in_class(m, cls_)]

    def allergy_conflicts(self) -> List[tuple]:
        dk = get_drug_knowledge()
        return [(a, m, lvl) for a in self.allergies for m in self.meds if (lvl := dk.allergy_conflict(a, m))]

    def ehr(self, inp: AgentInput, path: str, snippet: str) -> EvidenceRef:
        return ehr_ref(inp.patient_state.patient_id, inp.patient_state.version, path, snippet)
