"""Unsupported Claim Rate, reported twice: RAW (all claims agents emitted) and APPROVED (claims that reached the
approved output). With the provenance gate on, APPROVED is 0 by construction; RAW measures what the gate had to stop."""
from __future__ import annotations

from typing import Optional, Sequence

from src.contracts.evidence import ClinicalClaim
from src.provenance.gate import ProvenanceGate

_gate = ProvenanceGate()


def compute_ucr(claims: Sequence[ClinicalClaim]) -> Optional[float]:
    if not claims:
        return None
    return sum(not _gate.is_grounded(c) for c in claims) / len(claims)
