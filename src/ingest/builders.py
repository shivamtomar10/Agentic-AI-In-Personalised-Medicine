"""Compact PatientState builder shared by tests, eval scenarios and the API form parser."""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple, Union

from src.contracts.patient import (Allergy, Condition, LabValue, Medication, PatientDemographics,
                                   PatientState, VitalSign)
from src.units import analyte_key, canonical_unit

LabSpec = Union[float, Tuple[float, str]]
_DISPLAY = {"egfr": "eGFR", "hba1c": "HbA1c", "potassium": "Potassium", "creatinine": "Creatinine", "alt": "ALT", "ast": "AST",
            "glucose": "Glucose", "inr": "INR", "sodium": "Sodium", "hemoglobin": "Hemoglobin", "systolic_bp": "Systolic BP", "heart_rate": "Heart rate"}


def parse_dt(v: Any) -> datetime:
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    d = datetime.fromisoformat(str(v))
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _split_med(text: str) -> Tuple[str, str, str]:
    """'Metformin 1000 mg BID' -> ('Metformin', '1000 mg', 'BID')."""
    m = re.match(r"\s*([A-Za-z][A-Za-z\- ]*?)\s*(\d[\d.,/]*\s*(?:mg|mcg|g|units|iu|ml)?(?:/\w+)?)?\s*((?:qd|bid|tid|qid|od|daily|nightly|weekly|prn|q\d+h)\b.*)?$", text, re.I)
    if not m:
        return text.strip(), "unspecified", "unspecified"
    return m.group(1).strip(), (m.group(2) or "unspecified").strip(), (m.group(3) or "unspecified").strip()


def build_state(patient_id: str, birth_date: str = "1960-01-01", gender: str = "unknown", *, timestamp: Any = None, version: int = 1,
                encounter_id: Optional[str] = None, conditions: Iterable = (), meds: Iterable = (), allergies: Iterable = (),
                labs: Optional[Dict[str, LabSpec]] = None, vitals: Optional[Dict[str, LabSpec]] = None, notes: Iterable = (),
                race: Optional[str] = None) -> PatientState:
    ts = parse_dt(timestamp or "2026-01-01")
    conds: List[Condition] = []
    for c in conditions:
        if isinstance(c, str):
            code, name, status = (re.search(r"\(([^)]+)\)", c).group(1) if re.search(r"\(([^)]+)\)", c) else c[:12].upper().replace(" ", "_")), re.sub(r"\s*\([^)]*\)", "", c).strip(), "active"
        else:
            code, name, status = (tuple(c) + ("active",))[:3]
        conds.append(Condition(code=code, display_name=name, clinical_status=status, onset_date=ts))
    mlist: List[Medication] = []
    for m in meds:
        if isinstance(m, str):
            n, d, f = _split_med(m)
        else:
            n, d, f = (tuple(m) + ("unspecified", "unspecified"))[:3]
        mlist.append(Medication(name=n, dosage=d, frequency=f, start_date=ts, is_active=True))
    alist = [Allergy(substance=a) if isinstance(a, str) else Allergy(**a) for a in allergies]

    def lab(name: str, spec: LabSpec) -> LabValue:
        v, u = (spec, canonical_unit(name)) if not isinstance(spec, tuple) else spec
        return LabValue(code=analyte_key(name), display_name=_DISPLAY.get(analyte_key(name), name), value=float(v), unit=u, timestamp=ts)

    def vit(name: str, spec: LabSpec) -> VitalSign:
        v, u = (spec, canonical_unit(name)) if not isinstance(spec, tuple) else spec
        return VitalSign(code=analyte_key(name), display_name=_DISPLAY.get(analyte_key(name), name), value=float(v), unit=u, timestamp=ts)
    return PatientState(
        patient_id=patient_id, version=version, timestamp=ts, encounter_id=encounter_id,
        demographics=PatientDemographics(birth_date=str(birth_date), gender=gender, race=race),
        active_conditions=conds, active_medications=mlist, allergies=alist,
        latest_labs={_DISPLAY.get(analyte_key(k), k): lab(k, v) for k, v in (labs or {}).items()},
        vitals={_DISPLAY.get(analyte_key(k), k): vit(k, v) for k, v in (vitals or {}).items()},
        clinical_notes=list(notes),
    )
