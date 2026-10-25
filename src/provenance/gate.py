"""Provenance Gate (AGENTS.md Rule 4, Part A.3 G2).

Claims must carry >=1 *resolvable* EvidenceRef. Unsupported claims are routed to the clinician
'flagged' channel - never deleted silently and never placed in the approved output.
"""
from __future__ import annotations

from typing import Callable, List, Optional, Tuple

from src.contracts.evidence import ClaimStatus, ClinicalClaim, EvidenceRef
from src.knowledge.evidence_store import get_evidence_store

Resolver = Callable[[EvidenceRef], bool]


def default_resolver(ref: EvidenceRef) -> bool:
    if ref.id.startswith("ehr:"):
        return ref.source.value == "local_ehr"  # patient-derived; content checked by ValidationAgent
    return get_evidence_store().resolve(ref)


class ProvenanceGate:
    def __init__(self, resolver: Optional[Resolver] = None):
        self.resolver = resolver or default_resolver

    def is_grounded(self, claim: ClinicalClaim) -> bool:
        return bool(claim.evidence) and all(self.resolver(e) for e in claim.evidence)

    def partition(self, claims: List[ClinicalClaim]) -> Tuple[List[ClinicalClaim], List[ClinicalClaim]]:
        """Returns (supported, unsupported) as NEW objects; inputs are never mutated."""
        ok, bad = [], []
        for c in claims:
            if self.is_grounded(c):
                ok.append(c.model_copy(update={"status": ClaimStatus.SUPPORTED}))
            else:
                bad.append(c.model_copy(update={"status": ClaimStatus.UNSUPPORTED}))
        return ok, bad

    # --- backwards-compatible static API used by metrics/tests -----------------------------
    @staticmethod
    def filter_claims(claims: List[ClinicalClaim]) -> Tuple[List[ClinicalClaim], List[ClinicalClaim]]:
        return ProvenanceGate().partition(claims)

    @staticmethod
    def compute_unsupported_claim_rate(claims: List[ClinicalClaim]) -> float:
        if not claims:
            return 0.0
        _, bad = ProvenanceGate().partition(claims)
        return round(len(bad) / len(claims), 4)

    @staticmethod
    def compute_verification_report(claims: List[ClinicalClaim]) -> dict:
        """Returns detailed verification stats over a claim list."""
        if not claims:
            return {"total": 0, "supported": 0, "unsupported": 0, "support_rate": 1.0}
        ok, bad = ProvenanceGate().partition(claims)
        return {
            "total": len(claims),
            "supported": len(ok),
            "unsupported": len(bad),
            "support_rate": round(len(ok) / len(claims), 4),
        }

    def stale_evidence(self, claims: List[ClinicalClaim]) -> List[str]:
        store = get_evidence_store()
        return sorted({e.id for c in claims for e in c.evidence if store.is_stale(e)})
