"""Semantics-preserving perturbations of one encounter (RSI, Part C.9)."""
from __future__ import annotations

import random
from datetime import timedelta
from typing import Dict

from src.contracts.patient import PatientState
from src.units import analyte_key, convert_from_canonical

PERTURBATIONS = ["repeat_run", "reorder_fields", "equivalent_units", "irrelevant_demographics", "notes_paraphrase", "assay_jitter"]
_ALT_UNITS: Dict[str, str] = {"creatinine": "umol/L", "hba1c": "mmol/mol", "glucose": "mmol/L", "hemoglobin": "g/L"}
# decision thresholds the jitter must NOT cross (a crossing is a real change, tested separately at boundaries)
_DECISION = {"egfr": (60, 45, 30, 20, 15), "potassium": (5.2, 5.5), "hba1c": (7.0, 8.0), "alt": (120,), "systolic_bp": (130,)}


def _crosses(a: str, old: float, new: float) -> bool:
    return any((old >= t) != (new >= t) for t in _DECISION.get(a, ()))


def perturb(state: PatientState, kind: str, rng: random.Random, version: int) -> PatientState:
    s = state.model_copy(deep=True, update={"version": version, "state_hash": None, "previous_state_hash": None,
                                            "timestamp": state.timestamp + timedelta(days=1)})
    if kind == "repeat_run":
        return s
    if kind == "reorder_fields":
        return s.model_copy(update={"active_medications": list(reversed(s.active_medications)), "active_conditions": list(reversed(s.active_conditions)),
                                    "allergies": list(reversed(s.allergies)), "latest_labs": dict(reversed(list(s.latest_labs.items())))})
    if kind == "equivalent_units":
        labs = {}
        for k, lab in s.latest_labs.items():
            a = analyte_key(k)
            if a in _ALT_UNITS:
                u = _ALT_UNITS[a]
                labs[k] = lab.model_copy(update={"unit": u, "value": round(convert_from_canonical(a, lab.value, u), 4)})
            else:
                labs[k] = lab
        return s.model_copy(update={"latest_labs": labs})
    if kind == "irrelevant_demographics":
        return s.model_copy(update={"demographics": s.demographics.model_copy(update={"race": "Asian", "ethnicity": "Non-Hispanic"})})
    if kind == "notes_paraphrase":
        return s.model_copy(update={"clinical_notes": ["Patient seen for routine review, no new complaints reported."]})
    if kind == "assay_jitter":
        labs = {}
        for k, lab in s.latest_labs.items():
            a = analyte_key(k)
            new = round(lab.value * (1 + rng.uniform(-0.015, 0.015)), 3)
            labs[k] = lab.model_copy(update={"value": lab.value if _crosses(a, lab.value, new) else new})
        vit = {k: v.model_copy(update={"value": v.value if _crosses(analyte_key(k), v.value, v.value + d) else v.value + d})
               for k, v in s.vitals.items() for d in [rng.uniform(-3, 3)]}
        return s.model_copy(update={"latest_labs": labs, "vitals": vit})
    raise ValueError(kind)
