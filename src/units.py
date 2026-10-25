"""Unit normalisation so mg/dL vs umol/L never produces a false delta.
Implements Part C.3 ("Include unit normalisation"). Raises UnitError on unknown units (Rule 6).
"""
from __future__ import annotations

import re
from typing import Dict, Tuple


class UnitError(ValueError):
    """Unknown analyte or non-convertible unit."""


ALIASES: Dict[str, str] = {
    "egfr": "egfr", "estimated gfr": "egfr", "gfr": "egfr", "glomerular filtration rate": "egfr",
    "creatinine": "creatinine", "serum creatinine": "creatinine", "creat": "creatinine",
    "hba1c": "hba1c", "a1c": "hba1c", "hemoglobin a1c": "hba1c", "glycated hemoglobin": "hba1c",
    "potassium": "potassium", "k": "potassium", "k+": "potassium", "serum potassium": "potassium",
    "alt": "alt", "sgpt": "alt", "alanine aminotransferase": "alt",
    "ast": "ast", "sgot": "ast",
    "glucose": "glucose", "blood glucose": "glucose",
    "inr": "inr", "sodium": "sodium", "na": "sodium",
    "hemoglobin": "hemoglobin", "haemoglobin": "hemoglobin", "hgb": "hemoglobin",
    "systolic bp": "systolic_bp", "systolic_bp": "systolic_bp", "sbp": "systolic_bp",
    "heart rate": "heart_rate", "heart_rate": "heart_rate", "hr": "heart_rate",
}

CANONICAL_UNIT: Dict[str, str] = {
    "egfr": "mL/min/1.73m2", "creatinine": "mg/dL", "hba1c": "%", "potassium": "mmol/L",
    "alt": "U/L", "ast": "U/L", "glucose": "mg/dL", "inr": "", "sodium": "mmol/L",
    "hemoglobin": "g/dL", "systolic_bp": "mmHg", "heart_rate": "bpm",
}

# unit token -> (factor, offset) to canonical: canonical = value * factor + offset
_TO_CANON: Dict[str, Dict[str, Tuple[float, float]]] = {
    "egfr": {"ml/min/1.73m2": (1, 0), "ml/min/1.73m^2": (1, 0), "ml/min": (1, 0)},
    "creatinine": {"mg/dl": (1, 0), "umol/l": (1 / 88.4, 0), "micromol/l": (1 / 88.4, 0)},
    "hba1c": {"%": (1, 0), "percent": (1, 0), "mmol/mol": (1 / 10.929, 2.15)},
    "potassium": {"mmol/l": (1, 0), "meq/l": (1, 0)},
    "alt": {"u/l": (1, 0), "iu/l": (1, 0)},
    "ast": {"u/l": (1, 0), "iu/l": (1, 0)},
    "glucose": {"mg/dl": (1, 0), "mmol/l": (18.016, 0)},
    "inr": {"": (1, 0), "ratio": (1, 0)},
    "sodium": {"mmol/l": (1, 0), "meq/l": (1, 0)},
    "hemoglobin": {"g/dl": (1, 0), "g/l": (0.1, 0)},
    "systolic_bp": {"mmhg": (1, 0)},
    "heart_rate": {"bpm": (1, 0), "/min": (1, 0)},
}


def analyte_key(name: str) -> str:
    n = re.sub(r"\s+", " ", name.strip().lower())
    return ALIASES.get(n, n.replace(" ", "_"))


def _unit_token(unit: str) -> str:
    u = (unit or "").strip().lower().replace("µ", "u").replace("μ", "u").replace("²", "2").replace(" ", "")
    return u


def to_canonical(analyte: str, value: float, unit: str) -> float:
    key = analyte_key(analyte)
    table = _TO_CANON.get(key)
    if table is None:
        return float(value)  # analyte without a conversion table: unit must be trusted as-is
    tok = _unit_token(unit)
    if tok not in table:
        raise UnitError(f"Cannot convert unit '{unit}' for analyte '{analyte}'")
    f, o = table[tok]
    return round(float(value) * f + o, 4)


def canonical_unit(analyte: str) -> str:
    return CANONICAL_UNIT.get(analyte_key(analyte), "")


def convert_from_canonical(analyte: str, value: float, unit: str) -> float:
    """Inverse conversion, used by the eval harness to build 'equivalent unit' perturbations."""
    key = analyte_key(analyte)
    f, o = _TO_CANON[key][_unit_token(unit)]
    return (value - o) / f
