#!/usr/bin/env python3
"""Runs every agent on a rich synthetic patient and prints a per-agent PASS/FAIL report. Copy the output to share it."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from src.contracts.agent_io import ExecutionMode  # noqa: E402
from src.ingest.builders import build_state  # noqa: E402
from src.orchestrator import PipelineRunner  # noqa: E402

p = build_state("CHECK-1", "1955-03-01", "male", timestamp="2026-01-10", version=1, conditions=["Type 2 diabetes mellitus (E11.9)", "Hypertension (I10)"],
                meds=["Metformin 1000 mg BID", "Lisinopril 20 mg QD", "Warfarin 5 mg QD", "Ibuprofen 400 mg TID", "Amoxicillin 500 mg TID", "Ondansetron 4 mg TID", "Azithromycin 250 mg QD"],
                allergies=["Penicillin (rash)"], labs={"eGFR": 24, "Potassium": 5.8, "HbA1c": 9.4, "Creatinine": 2.6, "INR": 2.4}, vitals={"SBP": 148})
rn = PipelineRunner()
r = rn.run(p, mode=ExecutionMode.FULL_RERUN)
rec, out = r.recommendation, r.outputs
checks = [
    ("patient_data", len(out["patient_data"].claims) > 10, f"{len(out['patient_data'].claims)} normalised facts"),
    ("ehr_analysis", len(out["ehr_analysis"].claims) >= 1, "trajectory claims produced"),
    ("diagnostic", len(out["diagnostic"].differential) >= 2, f"{len(out['diagnostic'].differential)} differential items"),
    ("medical_knowledge", len(out["medical_knowledge"].evidence_pool) > 3, f"{len(out['medical_knowledge'].evidence_pool)} evidence passages"),
    ("treatment", any(a.action_type == "discontinue" and a.target == "metformin" for a in out["treatment"].actions), f"{len(out['treatment'].actions)} actions incl. stop metformin"),
    ("risk_assessment", {"RF-METFORMIN-EGFR30", "RF-DDI-IBUPROFEN-WARFARIN"} <= {f.flag_id for f in out["risk_assessment"].risk_flags} and any(f.category == "allergy" and "amoxicillin" in f.involved for f in out["risk_assessment"].risk_flags), f"{len(out['risk_assessment'].risk_flags)} risk flags"),
    ("validation", rec.validation_report is not None and rec.validation_report.passed, f"{rec.validation_report.checks_run} checks, {len(rec.validation_report.findings)} findings"),
    ("explanation", "DECISION SUPPORT ONLY" in rec.explanation, f"{len(rec.explanation)} chars"),
]
bad = 0
print("Agent                 Result  Detail")
for n, ok, d in checks:
    bad += not ok
    print(f"{n:21} {'PASS' if ok else 'FAIL':6}  {d}")
print(f"\nInvocations: {rec.metrics.agent_invocations} (reasoning {rec.metrics.reasoning_invocations}, context {rec.metrics.context_invocations}, safety {rec.metrics.safety_invocations})")
print("Every approved action cites evidence:", all(a.claim_ids for a in rec.clinical_actions), "| deferred:", rec.deferred)
print("\nALL 8 AGENTS OK" if not bad else f"\n{bad} AGENT(S) FAILED")
sys.exit(1 if bad else 0)
