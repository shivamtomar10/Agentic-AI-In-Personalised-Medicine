"""Risk-coverage curve and AURC for calibrated abstention (selective prediction)."""
from __future__ import annotations

from typing import Dict, List, Sequence, Tuple


def risk_coverage_curve(confidences: Sequence[float], correct: Sequence[bool]) -> List[Tuple[float, float, float]]:
    """[(threshold, coverage, risk)] answering every case with confidence >= threshold. risk = error rate among answered."""
    pts = []
    for t in sorted(set(confidences)):
        ans = [c for conf, c in zip(confidences, correct) if conf >= t]
        pts.append((t, len(ans) / len(confidences), (sum(1 for c in ans if not c) / len(ans)) if ans else 0.0))
    return pts


def aurc(confidences: Sequence[float], correct: Sequence[bool]) -> float:
    """Area under the risk-coverage curve (lower is better) by ranking cases by descending confidence."""
    if not confidences:
        return float("nan")
    order = sorted(range(len(confidences)), key=lambda i: -confidences[i])
    err = 0
    area = 0.0
    for k, i in enumerate(order, 1):
        err += 0 if correct[i] else 1
        area += err / k
    return area / len(order)
