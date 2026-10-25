"""Emergency red-flag detector: short-circuits the pipeline (Part C.9 #4). Conservative keyword + vital rules.

This is a triage safety net, NOT a diagnostic tool; false positives are acceptable, false negatives are not.
"""
from __future__ import annotations

import re
from typing import Iterable, List

from src.contracts.patient import PatientState

_PATTERNS = {
    "Possible acute coronary syndrome (chest pain / pressure)": r"chest (pain|pressure|tightness)|crushing",
    "Breathing difficulty": r"(shortness of breath|can'?t breathe|difficulty breathing|struggling to breathe)",
    "Possible stroke (sudden weakness, facial droop, speech loss)": r"(facial droop|face droop|slurred speech|sudden (weakness|numbness)|worst headache)",
    "Possible anaphylaxis": r"(anaphyla|throat (swelling|closing)|swollen (tongue|lips))",
    "Suicidal thoughts / self-harm": r"(suicid|kill myself|end my life|self[- ]harm|want to die)",
    "Loss of consciousness / seizure": r"(passed out|unconscious|seizure|fainted)",
    "Severe bleeding": r"(vomiting blood|coughing (up )?blood|black tarry stool|bleeding (heavily|won'?t stop))",
}


def detect_red_flags(texts: Iterable[str], state: PatientState | None = None) -> List[str]:
    hits = [label for label, pat in _PATTERNS.items() if any(re.search(pat, (t or "").lower()) for t in texts)]
    if state is not None:
        v = {k.lower(): x.value for k, x in state.vitals.items()}
        sbp = v.get("systolic bp", v.get("systolic_bp"))
        hr = v.get("heart rate", v.get("heart_rate"))
        if sbp is not None and (sbp >= 180 or sbp < 90):
            hits.append(f"Unstable blood pressure (systolic {sbp:g} mmHg)")
        if hr is not None and (hr >= 130 or hr < 40):
            hits.append(f"Unstable heart rate ({hr:g} bpm)")
    return hits


EMERGENCY_TEXT = ("Possible medical emergency detected. This tool is decision support only and cannot provide emergency care: "
                  "seek immediate clinical evaluation or call your local emergency number now.")
