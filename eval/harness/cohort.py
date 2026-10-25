"""Seeded SYNTHETIC longitudinal cohort simulator (NOT Synthea; see docs/data.md).

Each encounter is either ground-truth 'signal' (a clinically meaningful event drawn from EVENTS) or 'noise'
(assay-scale jitter + list reordering). The signal probability p_signal is a MODELLING ASSUMPTION; efficiency
results are therefore reported as a function of it, never as a single number.
"""
from __future__ import annotations

import copy
import random
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Callable, Dict, List, Tuple

from src.contracts.patient import PatientState
from src.ingest.builders import build_state

ARCH = [
    dict(gender="male", cond=["Type 2 diabetes mellitus (E11.9)", "Hypertension (I10)"], meds=["Metformin 1000 mg BID", "Lisinopril 20 mg QD"],
         labs=dict(eGFR=78, Potassium=4.3, HbA1c=6.8, ALT=26, Creatinine=1.0), sbp=124),
    dict(gender="female", cond=["Hypertension (I10)"], meds=["Amlodipine 5 mg QD", "Atorvastatin 20 mg QD"], labs=dict(eGFR=88, Potassium=4.1, HbA1c=5.6, ALT=24, Creatinine=0.9), sbp=122),
    dict(gender="male", cond=["Chronic kidney disease (N18.3)", "Type 2 diabetes mellitus (E11.9)"], meds=["Metformin 500 mg BID", "Losartan 50 mg QD"],
         labs=dict(eGFR=52, Potassium=4.7, HbA1c=7.2, ALT=30, Creatinine=1.5), sbp=128),
    dict(gender="female", cond=["Type 2 diabetes mellitus (E11.9)"], meds=["Metformin 1000 mg BID", "Glimepiride 2 mg QD"], labs=dict(eGFR=66, Potassium=4.4, HbA1c=7.6, ALT=27, Creatinine=1.0), sbp=118),
]


def _cross_unsafe(a: str, old: float, new: float) -> bool:
    return ((a == "eGFR" and any(old >= t > new for t in (60, 45, 30, 20, 15))) or (a == "Potassium" and old <= 5.5 < new) or (a == "Potassium" and old <= 5.2 < new)
            or (a == "HbA1c" and any(old < t <= new for t in (7.0, 8.0))) or (a == "ALT" and old < 120 <= new))


def _ev_egfr(sp, r): sp["labs"]["eGFR"] = max(8.0, round(sp["labs"]["eGFR"] - r.uniform(14, 28), 1)); sp["labs"]["Creatinine"] = round(sp["labs"]["Creatinine"] * 1.6, 2); return "eGFR decline"
def _ev_hba1c(sp, r): sp["labs"]["HbA1c"] = round(sp["labs"]["HbA1c"] + r.uniform(1.0, 2.2), 1); return "HbA1c rise"
def _ev_k(sp, r):
    if sp["labs"]["Potassium"] >= 5.5: return None
    sp["labs"]["Potassium"] = round(r.uniform(5.7, 6.3), 1); return "potassium spike"
def _ev_alt(sp, r):
    if sp["labs"]["ALT"] >= 120: return None
    sp["labs"]["ALT"] = round(r.uniform(130, 200)); return "ALT >= 3xULN"
def _ev_allergy(sp, r):
    a = r.choice(["Penicillin (rash)", "Sulfonamide (hives)"])
    if a in sp["allergies"]: return None
    sp["allergies"] = sp["allergies"] + [a]; return "new allergy"
def _ev_med(sp, r):
    m = r.choice(["Warfarin 5 mg QD", "Ibuprofen 400 mg TID", "Ondansetron 4 mg TID", "Azithromycin 250 mg QD", "Diphenhydramine 25 mg QD"])
    if m.split()[0].lower() in " ".join(sp["meds"]).lower(): return None
    sp["meds"] = sp["meds"] + [m]; return "new medication"
def _ev_dx(sp, r):
    if any("Heart failure" in c for c in sp["cond"]): return None
    sp["cond"] = sp["cond"] + ["Heart failure (I50.9)"]
    return "new diagnosis"
def _ev_resolve(sp, r):
    if not any(isinstance(c, str) and "Hypertension" in c for c in sp["cond"]): return None
    sp["cond"] = [(("I10", "Hypertension", "resolved") if isinstance(c, str) and "Hypertension" in c else c) for c in sp["cond"]]; return "condition resolved"


EVENTS: List[Callable] = [_ev_egfr, _ev_hba1c, _ev_k, _ev_alt, _ev_allergy, _ev_med, _ev_dx, _ev_resolve]


@dataclass
class Encounter:
    state: PatientState
    is_signal: bool
    event: str


def _state(pid, sp, birth, gender, when, v):
    return build_state(pid, birth, gender, timestamp=when, version=v, encounter_id=f"ENC-{v}", conditions=[tuple(c) if isinstance(c, tuple) else c for c in sp["cond"]],
                       meds=sp["meds"], allergies=sp["allergies"], labs=sp["labs"], vitals={"SBP": sp["sbp"]})


def simulate_patient(seed: int, idx: int, n_enc: int, p_signal: float) -> List[Encounter]:
    r = random.Random(f"{seed}-{idx}-{p_signal}")
    a = copy.deepcopy(r.choice(ARCH))
    sp = dict(cond=a["cond"], meds=a["meds"], allergies=[], labs=a["labs"], sbp=a["sbp"])
    birth = f"{r.randint(1945, 1985)}-{r.randint(1, 12):02d}-15"
    pid = f"SYN-{seed}-{idx}"
    t0 = date(2024, 1, 15)
    out = [Encounter(_state(pid, sp, birth, a["gender"], t0.isoformat(), 1), False, "baseline")]
    for v in range(2, n_enc + 1):
        when = (t0 + timedelta(days=91 * (v - 1))).isoformat()
        sp = copy.deepcopy(sp)
        prev_when = t0 + timedelta(days=91 * (v - 2))
        b65 = date(int(birth[:4]) + 65, int(birth[5:7]), 15)
        if prev_when < b65 <= date.fromisoformat(when):  # a real, rule-relevant demographic event (independent of the engine)
            out.append(Encounter(_state(pid, sp, birth, a["gender"], when, v), True, "age crosses 65"))
            continue
        ev = None
        if r.random() < p_signal:
            ev = r.choice(EVENTS)(sp, r)
        if ev:
            out.append(Encounter(_state(pid, sp, birth, a["gender"], when, v), True, ev))
        else:
            for k, band in (("eGFR", 3.0), ("Potassium", 0.2), ("HbA1c", 0.3), ("ALT", 5.0), ("Creatinine", 0.1)):
                new = round(sp["labs"][k] + r.uniform(-0.6, 0.6) * band, 2)
                if not _cross_unsafe(k, sp["labs"][k], new):
                    sp["labs"][k] = new
            sp["sbp"] = round(sp["sbp"] + r.uniform(-5, 5))
            if sp["sbp"] >= 130 > a["sbp"]:
                sp["sbp"] = a["sbp"]
            r.shuffle(sp["meds"])
            out.append(Encounter(_state(pid, sp, birth, a["gender"], when, v), False, "noise"))
    return out
