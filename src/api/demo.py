"""Synthetic demo patient (3 encounters) used by the clinician console's 'Load demo' button."""
from __future__ import annotations

from typing import List

from src.contracts.patient import PatientState
from src.ingest.builders import build_state

BASE = dict(birth_date="1955-03-01", gender="male", conditions=["Type 2 diabetes mellitus (E11.9)", "Hypertension (I10)"],
            meds=["Metformin 1000 mg BID", "Lisinopril 20 mg QD", "Ibuprofen 400 mg TID"], allergies=[])


def demo_encounters(pid: str) -> List[PatientState]:
    return [
        build_state(pid, timestamp="2026-01-10", version=1, encounter_id="ENC-1", labs={"eGFR": 72, "Potassium": 4.4, "HbA1c": 6.9, "Creatinine": 1.0}, vitals={"SBP": 126}, **BASE),
        # routine follow-up: everything inside measurement noise -> selective mode carries reasoning forward
        build_state(pid, timestamp="2026-04-12", version=2, encounter_id="ENC-2", labs={"eGFR": 71, "Potassium": 4.5, "HbA1c": 6.8, "Creatinine": 1.0}, vitals={"SBP": 128}, **BASE),
        # deterioration: renal decline, hyperkalaemia, poor glycaemic control, new penicillin allergy + amoxicillin
        build_state(pid, timestamp="2026-07-20", version=3, encounter_id="ENC-3", labs={"eGFR": 24, "Potassium": 5.8, "HbA1c": 9.4, "Creatinine": 2.6}, vitals={"SBP": 148},
                    **{**BASE, "allergies": ["Penicillin (rash)"], "meds": BASE["meds"] + ["Amoxicillin 500 mg TID"]}),
    ]
