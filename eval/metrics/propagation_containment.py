"""Propagation Containment Rate. FIX: the denominator is ALL injected faults (the scaffold counted only
detected ones and returned 1.0 when nothing was detected, so a useless validator scored perfectly)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional, Sequence


@dataclass
class FaultOutcome:
    kind: str
    point: str
    detected: bool     # flagged by ValidationAgent or blocked by the provenance gate
    contained: bool    # corrupted artefact absent from approved output


def compute_pcr(outcomes: Sequence[FaultOutcome]) -> Optional[float]:
    return None if not outcomes else sum(o.contained for o in outcomes) / len(outcomes)


def compute_detection_rate(outcomes: Sequence[FaultOutcome]) -> Optional[float]:
    return None if not outcomes else sum(o.detected for o in outcomes) / len(outcomes)


def breakdown(outcomes: Sequence[FaultOutcome]) -> Dict[str, Dict[str, Dict[str, float]]]:
    """{kind: {point: {n, pcr}}}"""
    out: Dict[str, Dict[str, Dict[str, float]]] = {}
    for o in outcomes:
        d = out.setdefault(o.kind, {}).setdefault(o.point, {"n": 0, "contained": 0})
        d["n"] += 1
        d["contained"] += int(o.contained)
    return {k: {p: {"n": v["n"], "pcr": v["contained"] / v["n"]} for p, v in pts.items()} for k, pts in out.items()}
