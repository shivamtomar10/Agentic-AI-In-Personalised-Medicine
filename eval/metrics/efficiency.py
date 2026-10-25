"""Efficiency: agent invocations and latency, selective vs full re-run."""
from __future__ import annotations

from typing import Optional, Sequence


def mean(xs: Sequence[float]) -> Optional[float]:
    return None if not xs else sum(xs) / len(xs)


def reduction(baseline: Sequence[float], selective: Sequence[float]) -> Optional[float]:
    b, s = mean(baseline), mean(selective)
    return None if not b else 1.0 - (s / b)
