#!/usr/bin/env python3
"""Generate a seeded SYNTHETIC longitudinal cohort (JSON of PatientState sequences).

This is our own simulator (eval/harness/cohort.py), NOT Synthea. It exists so the evaluation and demos are
reproducible offline. See docs/data.md for how to substitute Synthea/FHIR data (loader not implemented).
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from eval.harness.cohort import simulate_patient  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--count", type=int, default=500)
ap.add_argument("--encounters", type=int, default=12)
ap.add_argument("--p-signal", type=float, default=0.2)
ap.add_argument("--seed", type=int, default=42)
a = ap.parse_args()
out = Path(__file__).resolve().parents[1] / "data" / "synthetic" / f"cohort_seed{a.seed}.json"
out.parent.mkdir(parents=True, exist_ok=True)
rows = []
for i in range(a.count):
    enc = simulate_patient(a.seed, i, a.encounters, a.p_signal)
    rows.append({"patient_id": enc[0].state.patient_id, "encounters": [{"is_signal": e.is_signal, "event": e.event, "state": json.loads(e.state.model_dump_json())} for e in enc]})
out.write_text(json.dumps({"seed": a.seed, "p_signal": a.p_signal, "synthetic": True, "patients": rows}), encoding="utf-8")
print(f"wrote {a.count} synthetic patients x {a.encounters} encounters -> {out}")
