"""Recommendation Stability Index (Part A.3 / C.9): semantic, not textual, equality."""
from __future__ import annotations

from typing import Optional, Sequence, Tuple

from src.contracts.recommendation import Recommendation


def recommendation_signature(rec: Recommendation) -> tuple:
    """(set of (action_type,target), set of approved risk-flag ids, deferred). Rationale text is deliberately ignored."""
    return (frozenset((a.action_type, a.target) for a in rec.clinical_actions), frozenset(f.flag_id for f in rec.risk_flags), rec.deferred)


def compute_rsi(pairs: Sequence[Tuple[Recommendation, Recommendation]]) -> Optional[float]:
    """Fraction of (reference, perturbed) pairs whose semantic signatures are identical. None if no pairs."""
    if not pairs:
        return None
    return sum(recommendation_signature(a) == recommendation_signature(b) for a, b in pairs) / len(pairs)
