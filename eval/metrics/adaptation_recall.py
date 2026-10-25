"""Adaptation Recall: does the recommendation change appropriately when a real clinical change occurs?"""
from __future__ import annotations

from typing import Dict, List, Optional, Sequence, Tuple

from src.contracts.recommendation import Recommendation


def check_expectations(rec: Recommendation, expect: Dict) -> Tuple[bool, List[str]]:
    """Ground-truth check. expect: required_flags (id prefixes), required_actions [[type,target-substr]], forbidden_actions."""
    misses: List[str] = []
    if rec.deferred:
        misses.append("recommendation was deferred")
    ids = [f.flag_id for f in rec.risk_flags]
    for p in expect.get("required_flags", []):
        if not any(i.startswith(p) for i in ids):
            misses.append(f"missing risk flag {p}*")
    acts = [(a.action_type, a.target.lower()) for a in rec.clinical_actions]
    types = lambda t: t if isinstance(t, list) else [t]     # a type may be a list = "any of these verbs"
    for t, sub in expect.get("required_actions", []):
        if not any(at in types(t) and sub.lower() in tg for at, tg in acts):
            misses.append(f"missing action {'/'.join(types(t))} {sub}")
    for t, sub in expect.get("forbidden_actions", []):
        if any(at in types(t) and sub.lower() in tg for at, tg in acts):
            misses.append(f"forbidden action present: {'/'.join(types(t))} {sub}")
    for cat, drugs in expect.get("required_flags_involving", []):
        if not any(f.category == cat and all(d.lower() in [x.lower() for x in f.involved] for d in drugs) for f in rec.risk_flags):
            misses.append(f"missing {cat} flag involving {drugs}")
    return (not misses), misses


def compute_adaptation_recall(outcomes: Sequence[bool]) -> Optional[float]:
    return None if not outcomes else sum(outcomes) / len(outcomes)
