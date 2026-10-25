"""Orchestration policy: agent groups, budgets, conflict resolution (Part C.7)."""
from __future__ import annotations

from typing import List

from src.contracts.recommendation import ClinicalAction, ConflictRecord, RiskFlag, RiskSeverity
from src.knowledge.drugs import get_drug_knowledge

CONTEXT_AGENTS = ("patient_data", "ehr_analysis")
REASONING_AGENTS = ("diagnostic", "medical_knowledge", "treatment", "risk_assessment")
SAFETY_AGENTS = ("validation", "explanation")
FULL_PIPELINE_ORDER = list(CONTEXT_AGENTS) + list(REASONING_AGENTS) + list(SAFETY_AGENTS)
CRITICAL_FOR_ABSTENTION = ("treatment", "risk_assessment", "medical_knowledge")


class InvocationBudgetExceeded(RuntimeError):
    pass


class PipelineTimeout(RuntimeError):
    pass


def resolve_conflicts(actions: List[ClinicalAction], flags: List[RiskFlag]) -> List[ConflictRecord]:
    """Surface (never hide) disagreement: an affirmative action on a drug that a high/critical flag contraindicates."""
    dk = get_drug_knowledge()
    out: List[ConflictRecord] = []
    for a in actions:
        if a.action_type not in ("prescribe", "continue", "adjust_dose"):
            continue
        g = dk.normalise(a.target)
        for f in flags:
            if f.severity in (RiskSeverity.HIGH, RiskSeverity.CRITICAL) and f.category in ("contraindication", "allergy", "pregnancy") and g in f.involved:
                out.append(ConflictRecord(agents=[a.source_agent or "treatment", f.source_agent or "risk_assessment"],
                                          position_a=f"{a.action_type} {a.target}", position_b=f.description,
                                          evidence_a=list(a.claim_ids), evidence_b=[e.id for e in f.evidence]))
    return out
