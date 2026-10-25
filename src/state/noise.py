"""Shared measurement-noise bands (from config/significance_rules.yaml) used by DeltaEngine AND ValidationAgent,
so 'within measurement noise' means the same thing everywhere."""
from __future__ import annotations

from functools import lru_cache

import yaml

from src.config import get_settings


@lru_cache(maxsize=1)
def _rules() -> dict:
    return yaml.safe_load((get_settings().config_dir / "significance_rules.yaml").read_text(encoding="utf-8"))


def noise_band(analyte: str, value: float) -> float:
    cfg = _rules()["labs"].get(analyte)
    if not cfg:
        return _rules()["defaults"]["generic_noise_rel"] * abs(value)
    n = cfg.get("noise", {})
    return max(n.get("abs", 0.0), n.get("rel", 0.0) * abs(value))
