"""Canonicalised, read-only view over a PatientState (units normalised, names generic)."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List, Optional, Set, Tuple

from src.contracts.patient import PatientState
from src.knowledge.drugs import get_drug_knowledge
from src.units import UnitError, analyte_key, to_canonical


def _utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


class PatientView:
    def __init__(self, state: PatientState):
        self.state = state
        dk = get_drug_knowledge()
        self.labs: Dict[str, float] = {}
        self.unit_errors: List[str] = []
        for name, lab in state.latest_labs.items():
            try:
                self.labs[analyte_key(name)] = to_canonical(name, lab.value, lab.unit)
            except UnitError as e:
                self.unit_errors.append(str(e))
        self.vitals: Dict[str, float] = {}
        for name, v in state.vitals.items():
            try:
                self.vitals[analyte_key(name)] = to_canonical(name, v.value, v.unit)
            except UnitError as e:
                self.unit_errors.append(str(e))
        self.meds: List[str] = sorted({dk.normalise(m.name) for m in state.active_medications if m.is_active})
        self.med_classes: Set[str] = set().union(*[dk.classes_of(m) for m in self.meds]) if self.meds else set()
        self.allergies: List[str] = [a.substance.lower() for a in state.allergies]
        self.conditions: List[str] = [c.display_name.lower() for c in state.active_conditions if c.clinical_status in ("active", "recurrence", "relapse")]
        self.condition_codes: Set[str] = {c.code.upper() for c in state.active_conditions if c.clinical_status in ("active", "recurrence", "relapse")}

    def age(self) -> float:
        b = datetime.fromisoformat(self.state.demographics.birth_date)
        ref = _utc(self.state.timestamp)
        return (ref - _utc(b)).days / 365.25

    def lab(self, key: str) -> Optional[float]:
        return self.labs.get(key)

    def has_condition(self, *keywords: str) -> bool:
        return any(k in c for c in self.conditions for k in keywords)

    def is_pregnant(self) -> bool:
        return self.has_condition("pregnan") or bool(self.condition_codes & {"Z33.1", "77386006"})

    def meds_in_class(self, cls: str) -> List[str]:
        dk = get_drug_knowledge()
        return [m for m in self.meds if dk.in_class(m, cls)]

    def allergy_conflicts(self) -> List[Tuple[str, str, str]]:
        """(allergy, med, level) for active meds only."""
        dk = get_drug_knowledge()
        out = []
        for a in self.allergies:
            for m in self.meds:
                lvl = dk.allergy_conflict(a, m)
                if lvl:
                    out.append((a, m, lvl))
        return out
