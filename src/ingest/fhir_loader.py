"""FHIR R4 bundle -> longitudinal PatientState snapshots (Synthea-shaped input).

Plain-JSON parsing (no fhir.resources dependency). One snapshot is produced per distinct Observation date; each snapshot
holds the latest lab per analyte up to that date, conditions with their status AS OF that date, medications active as of
that date, and allergies recorded by that date.

HONEST STATUS: verified only on a hand-written bundle (tests/test_fhir_loader.py). It has NOT been run on real Synthea
output, whose structure varies by module/version (extra resource types, coded-only medications, BP panels...). Expect to
adapt the LOINC map and medication-name extraction. Only synthetic FHIR data may be used (AGENTS.md Rule 2).
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from src.contracts.patient import PatientState
from src.ingest.builders import build_state

LOINC = {"33914-3": "egfr", "62238-1": "egfr", "48642-3": "egfr", "2160-0": "creatinine", "4548-4": "hba1c", "2823-3": "potassium", "1742-6": "alt", "1920-8": "ast",
         "2345-7": "glucose", "2339-0": "glucose", "2951-2": "sodium", "718-7": "hemoglobin", "6301-6": "inr", "8480-6": "systolic_bp", "8867-4": "heart_rate"}
VITALS = {"systolic_bp", "heart_rate"}


def _dt(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    d = datetime.fromisoformat(s.replace("Z", "+00:00"))
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _coding(cc: Dict[str, Any]) -> Tuple[str, str]:
    c = (cc.get("coding") or [{}])[0]
    return c.get("code", ""), c.get("display") or cc.get("text") or ""


def bundle_to_states(bundle: Dict[str, Any], patient_id: Optional[str] = None) -> List[PatientState]:
    res: Dict[str, List[dict]] = {}
    for e in bundle.get("entry", []):
        r = e.get("resource", {})
        res.setdefault(r.get("resourceType", ""), []).append(r)
    pats = res.get("Patient", [])
    if not pats:
        raise ValueError("Bundle contains no Patient resource")
    pt = pats[0]
    pid = re.sub(r"[^A-Za-z0-9_\-]", "-", patient_id or f"FHIR-{pt.get('id', 'unknown')}")[:40]
    obs: List[Tuple[datetime, str, float, str]] = []
    for o in res.get("Observation", []):
        when = _dt(o.get("effectiveDateTime") or (o.get("effectivePeriod") or {}).get("start"))
        if when is None:
            continue
        items = [(o.get("code", {}), o.get("valueQuantity"))] + [(c.get("code", {}), c.get("valueQuantity")) for c in o.get("component", [])]
        for code, vq in items:
            key = LOINC.get(_coding(code)[0])
            if key and vq and "value" in vq:
                obs.append((when, key, float(vq["value"]), vq.get("unit", "")))
    if not obs:
        raise ValueError("Bundle contains no mappable Observations")
    dates = sorted({o[0] for o in obs})
    states: List[PatientState] = []
    for v, d in enumerate(dates, 1):
        conds = []
        for c in res.get("Condition", []):
            code, name = _coding(c.get("code", {}))
            onset, ab = _dt(c.get("onsetDateTime")), _dt(c.get("abatementDateTime"))
            if onset is None or onset > d:
                continue
            status = "resolved" if (ab is not None and ab <= d) else "active"
            conds.append((code or name[:12], name, status))
        meds = []
        for m in res.get("MedicationRequest", []):
            when = _dt(m.get("authoredOn"))
            end = _dt(((m.get("dispenseRequest") or {}).get("validityPeriod") or {}).get("end"))
            if when is None or when > d or m.get("status") == "entered-in-error" or (end is not None and end <= d):
                continue
            cc = m.get("medicationCodeableConcept", {})
            name = _coding(cc)[1] or cc.get("text", "")
            dose = ((m.get("dosageInstruction") or [{}])[0]).get("text", "")
            meds.append(f"{name} {dose}".strip())
        allergies = []
        for a in res.get("AllergyIntolerance", []):
            rec = _dt(a.get("recordedDate") or a.get("onsetDateTime"))
            if rec is not None and rec <= d:
                allergies.append(_coding(a.get("code", {}))[1])
        labs: Dict[str, Tuple[float, str]] = {}
        vitals: Dict[str, Tuple[float, str]] = {}
        for when, key, val, unit in sorted(obs, key=lambda x: x[0]):
            if when <= d:
                (vitals if key in VITALS else labs)[key] = (val, unit)
        states.append(build_state(pid, (pt.get("birthDate") or "1970-01-01"), (pt.get("gender") or "unknown"), timestamp=d, version=v, encounter_id=f"ENC-{v}",
                                  conditions=conds, meds=meds, allergies=allergies, labs=labs, vitals=vitals))
    return states
