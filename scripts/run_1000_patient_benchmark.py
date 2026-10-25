from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd

from src.config import Settings
from src.contracts.agent_io import ExecutionMode
from src.ingest.builders import build_state
from src.llm import get_llm_client
from src.orchestrator import PipelineConfig, PipelineRunner

PROJECT_ROOT = Path(__file__).resolve().parents[1]

INPUT_FILE = PROJECT_ROOT / "caretrace_1000_evidence_grounded_patients.csv"
OUTPUT_DIR = PROJECT_ROOT / "eval" / "results"
OUTPUT_FILE = OUTPUT_DIR / "caretrace_1000_agent_answers.csv"


def clean_list(value):
    if pd.isna(value) or str(value).strip() == "":
        return []
    text = str(value).strip()
    if text.startswith("[") and text.endswith("]"):
        try:
            parsed = json.loads(text.replace("'", '"'))
            if isinstance(parsed, list):
                return parsed
        except Exception:
            pass
    return [x.strip() for x in text.split(";") if x.strip()]


def clean_float(value):
    if pd.isna(value) or str(value).strip() == "":
        return None
    try:
        return float(value)
    except Exception:
        return None


def build_patient_state(row):
    labs = {}
    for csv_name, state_name in [
        ("egfr", "eGFR"),
        ("hba1c", "HbA1c"),
        ("potassium", "Potassium"),
        ("creatinine", "Creatinine"),
        ("alt", "ALT"),
    ]:
        value = clean_float(row[csv_name])
        if value is not None:
            labs[state_name] = value

    sbp = clean_float(row["systolic_bp"])
    vitals = {}
    if sbp is not None:
        vitals["Systolic BP"] = sbp

    conditions = clean_list(row["health_conditions"])
    medicines = clean_list(row["medicines"])
    allergies = clean_list(row["allergies"])

    state = build_state(
        patient_id=str(row["patient_id"]),
        birth_date="1970-01-01",
        gender="unknown",
        timestamp="2026-01-01",
        version=1,
        encounter_id=f"BENCH-{row['patient_id']}",
        conditions=conditions,
        meds=medicines,
        allergies=allergies,
        labs=labs,
        vitals=vitals,
        notes=[str(row["reason_for_checkin"])] if not pd.isna(row["reason_for_checkin"]) else [],
    )
    return state


def recommendation_to_dict(rec):
    data = {
        "status": getattr(rec.status, "value", str(rec.status)),
        "confidence": rec.aggregate_confidence,
        "deferred": rec.deferred,
        "narrative": rec.explanation,
    }

    actions = []
    for action in rec.clinical_actions or []:
        actions.append({
            "action_type": action.action_type,
            "target": action.target,
            "details": action.details,
            "rationale": action.rationale,
            "urgency": action.urgency,
            "claim_ids": action.claim_ids,
            "source_type": getattr(action, "source_type", "rule"),
        })
    data["clinical_actions"] = actions

    risk_flags = []
    for flag in rec.risk_flags or []:
        risk_flags.append({
            "flag_id": flag.flag_id,
            "category": flag.category,
            "severity": getattr(flag.severity, "value", str(flag.severity)),
            "description": flag.description,
            "involved": flag.involved,
            "claim_id": flag.claim_id,
            "source_type": getattr(flag, "source_type", "rule"),
        })
    data["risk_flags"] = risk_flags
    data["validation_passed"] = rec.validation_report.passed if rec.validation_report is not None else None
    return data


def process_single_row(idx_row, mode_name, runner):
    index, row = idx_row
    patient_id = str(row["patient_id"])
    try:
        state = build_patient_state(row)
        result = runner.run(state, mode=ExecutionMode.FULL_RERUN)
        rec = result.recommendation

        if mode_name == "llm_only":
            rec.clinical_actions = [a for a in rec.clinical_actions if getattr(a, "source_type", "rule") == "llm"]
            rec.risk_flags = [f for f in rec.risk_flags if getattr(f, "source_type", "rule") == "llm"]

        rec_data = recommendation_to_dict(rec)

        return {
            "index": index,
            "patient_id": patient_id,
            "scenario": row["scenario"],
            "reference_expected_action": row["reference_expected_action"],
            "reference_expected_risk": row["reference_expected_risk"],
            "reference_review_required": row["reference_review_required"],
            "reference_source_family": row["reference_source_family"],
            "reference_source_record": row["reference_source_record"],
            "reference_rationale": row["reference_rationale"],
            "actual_status": rec_data["status"],
            "actual_confidence": rec_data["confidence"],
            "actual_deferred": rec_data["deferred"],
            "actual_narrative": rec_data["narrative"],
            "actual_clinical_actions": json.dumps(rec_data["clinical_actions"], default=str),
            "actual_risk_flags": json.dumps(rec_data["risk_flags"], default=str),
            "actual_validation_passed": rec_data["validation_passed"],
            "agent_invocations": rec.metrics.agent_invocations,
            "reasoning_invocations": rec.metrics.reasoning_invocations,
            "safety_replans": rec.metrics.safety_replans,
            "wall_clock_ms": rec.metrics.wall_clock_ms,
            "pipeline_error": "",
        }
    except Exception as exc:
        return {
            "index": index,
            "patient_id": patient_id,
            "scenario": row["scenario"],
            "reference_expected_action": row["reference_expected_action"],
            "reference_expected_risk": row["reference_expected_risk"],
            "reference_review_required": row["reference_review_required"],
            "reference_source_family": row["reference_source_family"],
            "reference_source_record": row["reference_source_record"],
            "reference_rationale": row["reference_rationale"],
            "actual_status": "",
            "actual_confidence": "",
            "actual_deferred": "",
            "actual_narrative": "",
            "actual_clinical_actions": "",
            "actual_risk_flags": "",
            "actual_validation_passed": "",
            "agent_invocations": "",
            "reasoning_invocations": "",
            "safety_replans": "",
            "wall_clock_ms": "",
            "pipeline_error": f"{type(exc).__name__}: {exc}",
        }


def run_benchmark_for_mode(mode_name: str, df: pd.DataFrame, out_path: Path):
    print(f"\n============================================================", flush=True)
    print(f"RUNNING BENCHMARK MODE: {mode_name.upper()}", flush=True)
    print(f"============================================================", flush=True)

    llm_client = None
    if mode_name in ("hybrid", "llm_only"):
        s = Settings(llm_provider="ollama")
        try:
            llm_client = get_llm_client(s)
        except Exception:
            llm_client = None

    if mode_name == "rules_only":
        pcfg = PipelineConfig(llm_mode="off")
    elif mode_name == "hybrid":
        pcfg = PipelineConfig(llm_mode="hybrid", llm=llm_client)
    elif mode_name == "llm_only":
        pcfg = PipelineConfig(llm_mode="hybrid", llm=llm_client)

    runner = PipelineRunner(pcfg)
    workers = 16 if mode_name in ("hybrid", "llm_only") else 8

    with ThreadPoolExecutor(max_workers=workers) as executor:
        items = list(df.iterrows())
        results = list(executor.map(lambda item: process_single_row(item, mode_name, runner), items))

    results.sort(key=lambda r: r["index"])
    for r in results:
        del r["index"]

    output_df = pd.DataFrame(results)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    output_df.to_csv(out_path, index=False)
    print(f"Saved {mode_name} results to {out_path} ({len(results)} patients)", flush=True)


def main():
    parser = argparse.ArgumentParser(description="Run 1,000-patient benchmark")
    parser.add_argument("--mode", choices=["rules_only", "llm_only", "hybrid", "all"], default="all", help="Benchmark mode to run")
    args = parser.parse_args()

    if not INPUT_FILE.exists():
        raise FileNotFoundError(f"Input CSV not found: {INPUT_FILE}")

    df = pd.read_csv(INPUT_FILE)
    print(f"Loaded {len(df)} benchmark patients from {INPUT_FILE.name}", flush=True)

    modes_to_run = ["rules_only", "llm_only", "hybrid"] if args.mode == "all" else [args.mode]

    for mode in modes_to_run:
        out_csv = OUTPUT_DIR / f"caretrace_1000_agent_answers_{mode}.csv"
        run_benchmark_for_mode(mode, df, out_csv)

        if mode in ("rules_only", "all"):
            output_df = pd.read_csv(out_csv)
            output_df.to_csv(OUTPUT_FILE, index=False)

    print("\nBenchmark runs complete! Run `python -m scripts.compare_benchmark_results` to view comparison table.", flush=True)


if __name__ == "__main__":
    main()