"""Pure-Python logistic-regression calibrator for abstention (no numpy/sklearn dependency).

Fits P(recommendation is correct | confidence features) on labelled cases and stores the weights in
config/abstention_weights.yaml. Features are standardised internally.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence

import yaml

from src.config import get_settings

FEATURES = ["agent", "coverage", "consistency", "recency", "completeness", "knowledge"]


def _sig(z: float) -> float:
    return 1.0 / (1.0 + math.exp(-max(-30.0, min(30.0, z))))


@dataclass
class LogisticCalibrator:
    weights: List[float]
    bias: float
    mean: List[float]
    std: List[float]
    features: List[str]

    def predict(self, feats: Dict[str, float]) -> float:
        z = self.bias + sum(w * (feats[f] - m) / s for w, f, m, s in zip(self.weights, self.features, self.mean, self.std))
        return round(_sig(z), 4)

    def to_dict(self) -> dict:
        return {"mode": "logistic", "features": self.features, "weights": [round(w, 6) for w in self.weights], "bias": round(self.bias, 6),
                "mean": [round(m, 6) for m in self.mean], "std": [round(s, 6) for s in self.std]}


def fit_logistic(X: Sequence[Dict[str, float]], y: Sequence[bool], features: Sequence[str] = FEATURES, l2: float = 0.05, lr: float = 0.3, epochs: int = 1500) -> LogisticCalibrator:
    n, d = len(X), len(features)
    mean = [sum(x[f] for x in X) / n for f in features]
    raw = [math.sqrt(sum((x[f] - m) ** 2 for x in X) / n) for f, m in zip(features, mean)]
    std = [r if r > 1e-3 else 1.0 for r in raw]      # constant features carry no signal; avoid dividing by ~0 at inference
    Z = [[(x[f] - m) / s for f, m, s in zip(features, mean, std)] for x in X]
    t = [1.0 if v else 0.0 for v in y]
    w, b = [0.0] * d, 0.0
    for _ in range(epochs):
        gw, gb = [0.0] * d, 0.0
        for z, ti in zip(Z, t):
            e = _sig(b + sum(wi * zi for wi, zi in zip(w, z))) - ti
            gb += e
            for j in range(d):
                gw[j] += e * z[j]
        b -= lr * gb / n
        for j in range(d):
            w[j] -= lr * (gw[j] / n + l2 * w[j])
    return LogisticCalibrator(w, b, mean, std, list(features))


def weights_path() -> Path:
    return get_settings().config_dir / "abstention_weights.yaml"


def load_calibrator(path: Optional[Path] = None) -> Optional[LogisticCalibrator]:
    p = path or weights_path()
    if not p.exists():
        return None
    d = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    if d.get("mode") != "logistic":
        return None
    return LogisticCalibrator(d["weights"], d["bias"], d["mean"], d["std"], d["features"])


def save_calibrator(c: LogisticCalibrator, path: Optional[Path] = None, note: str = "") -> None:
    p = path or weights_path()
    p.write_text("# Fitted by `python run.py calibrate` on SYNTHETIC labelled cases. " + note + "\n" + yaml.safe_dump(c.to_dict(), sort_keys=False), encoding="utf-8")
