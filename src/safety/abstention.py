"""Calibrated abstention (Part A.3 G4, C.7).

Two scores are produced for every run:
  * `aggregate`  - fixed hand-picked linear blend (agent 0.4, coverage 0.3, consistency 0.2, recency 0.1); the original design.
  * `calibrated` - P(correct) from a logistic model FITTED on labelled outcomes (src/safety/calibration.py), if configured.
The policy defers on min(calibrated, fixed) when a calibrator is configured, else on the fixed one.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

WEIGHTS = {"agent": 0.4, "coverage": 0.3, "consistency": 0.2, "recency": 0.1}


@dataclass
class ConfidenceBreakdown:
    agent: float
    coverage: float
    consistency: float
    recency: float
    aggregate: float
    completeness: float = 1.0      # 1 - share of critical labs missing (data-quality flags)
    knowledge: float = 1.0         # share of active medications the knowledge base actually covers
    calibrated: Optional[float] = None

    def features(self) -> Dict[str, float]:
        return {"agent": self.agent, "coverage": self.coverage, "consistency": self.consistency, "recency": self.recency,
                "completeness": self.completeness, "knowledge": self.knowledge}


def aggregate_confidence(agent_confidences: List[float], supported: int, total_claims: int, n_conflicts: int,
                         n_validation_faults: int, stale: int, total_evidence: int,
                         completeness: float = 1.0, knowledge: float = 1.0) -> ConfidenceBreakdown:
    agent = sum(agent_confidences) / len(agent_confidences) if agent_confidences else 0.0
    coverage = supported / total_claims if total_claims else 1.0
    consistency = max(0.0, 1.0 - 0.5 * n_conflicts - 0.5 * n_validation_faults)
    recency = 1.0 - (stale / total_evidence if total_evidence else 0.0)
    agg = (WEIGHTS["agent"] * agent + WEIGHTS["coverage"] * coverage + WEIGHTS["consistency"] * consistency + WEIGHTS["recency"] * recency)
    return ConfidenceBreakdown(round(agent, 4), round(coverage, 4), round(consistency, 4), round(recency, 4), round(agg, 4),
                               round(completeness, 4), round(knowledge, 4))


class AbstentionPolicy:
    """Defer (present findings, withhold recommendation) when the confidence score < threshold."""

    def __init__(self, threshold: float = 0.6, enabled: bool = True):
        self.threshold, self.enabled = threshold, enabled

    def score(self, b: ConfidenceBreakdown) -> float:
        # Hybrid: defer if EITHER the learned P(correct) or the fixed blend (which reacts to validation findings/conflicts) is low.
        return min(b.calibrated, b.aggregate) if b.calibrated is not None else b.aggregate

    def decide(self, b: ConfidenceBreakdown, critical_agent_abstained: Optional[str] = None) -> Optional[str]:
        if not self.enabled:
            return None
        if critical_agent_abstained:
            return f"Agent '{critical_agent_abstained}' abstained; insufficient basis for a recommendation"
        if self.score(b) < self.threshold:
            kind = "Calibrated confidence" if b.calibrated is not None else "Aggregate confidence"
            return f"{kind} {self.score(b):.2f} is below the abstention threshold {self.threshold:.2f}"
        return None
