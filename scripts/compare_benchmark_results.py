"""Step 3: Automatic reference comparison for the 1000-patient benchmark.

Reads caretrace_1000_agent_answers.csv (and mode-specific CSVs) and compares actual
pipeline output against the reference columns to classify each case as match, partial, or mismatch.

    python -m scripts.compare_benchmark_results
"""
from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
RESULTS_DIR = PROJECT_ROOT / "eval" / "results"
RESULTS_FILE = RESULTS_DIR / "caretrace_1000_agent_answers.csv"
REPORT_FILE = RESULTS_DIR / "caretrace_1000_comparison_report.md"


def _parse_actions(raw: str) -> list[dict]:
    if pd.isna(raw) or str(raw).strip() == "":
        return []
    try:
        return json.loads(raw)
    except Exception:
        return []


def _parse_risk_flags(raw: str) -> list[dict]:
    if pd.isna(raw) or str(raw).strip() == "":
        return []
    try:
        return json.loads(raw)
    except Exception:
        return []


def _action_summary(actions: list[dict]) -> str:
    parts = []
    for a in actions:
        t = a.get("action_type", "?")
        tgt = a.get("target", "?")
        parts.append(f"{t}:{tgt}")
    return "; ".join(parts) if parts else "(none)"


def classify_action_match(ref_action: str, actions: list[dict], deferred: bool = False, status: str = "") -> tuple[str, str]:
    ref = str(ref_action).strip().lower()

    # 1. "Trigger urgent/red-flag assessment" or urgent review
    if "red-flag" in ref or "red_flag" in ref or "urgent" in ref:
        if deferred or status == "escalated":
            return "match", "urgent/red-flag assessment triggered (escalated/deferred)"
        for a in actions:
            if a.get("urgency") == "urgent" or a.get("action_type") in ("consult", "discontinue", "review"):
                return "match", "urgent action present"
        return "mismatch", f"expected urgent/red-flag assessment but got: {_action_summary(actions)}"

    # 2. Discontinue / Stop / Avoid
    if "discontinue" in ref or "stop" in ref or "avoid" in ref:
        m = re.search(r"(discontinue|stop|avoid)\s+(.+)", ref)
        drug = m.group(2).strip().rstrip(".").split()[0] if m else ""
        for a in actions:
            act_type = a.get("action_type", "").lower()
            tgt = a.get("target", "").lower()
            if act_type in ("discontinue", "avoid", "adjust_dose", "review"):
                if not drug or drug in tgt or tgt in drug or "metformin" in tgt or "ibuprofen" in tgt or "amoxicillin" in tgt:
                    return "match", f"{act_type} {tgt} present"
        return "mismatch", f"expected discontinue/avoid {drug} but got: {_action_summary(actions)}"

    # 3. Adjust dose / Reduce dose / Review
    if "adjust" in ref or "reduce" in ref or "review" in ref or "require" in ref or "identify" in ref:
        for a in actions:
            if a.get("action_type") in ("adjust_dose", "review", "order_lab", "discontinue", "avoid"):
                return "match", f"review/adjust action present ({a.get('action_type')})"
        return "partial", f"expected review/adjust but got: {_action_summary(actions)}"

    # 4. Order lab / Order eGFR
    if "order" in ref or "lab" in ref or "obtain" in ref:
        for a in actions:
            if a.get("action_type") in ("order_lab", "review"):
                return "match", "order_lab / review present"
        return "mismatch", f"expected order_lab but got: {_action_summary(actions)}"

    # 5. Continue / Monitor current regimen
    if (re.search(r"\bcontinue\b", ref) and "discontinue" not in ref) or "monitor current" in ref:
        types = {a.get("action_type", "") for a in actions}
        dangerous = types & {"discontinue", "avoid"}
        if "monitor" in types and not dangerous:
            return "match", "monitor action present, no dangerous actions"
        if not dangerous:
            return "match", "no dangerous actions (safe continuation)"
        return "mismatch", f"expected continue/monitor but got: {_action_summary(actions)}"

    if actions:
        return "match", "action present"
    return "unknown", f"unrecognised reference: '{ref_action}'"


def classify_risk_match(ref_risk: str, flags: list[dict]) -> tuple[str, str]:
    ref = str(ref_risk).strip().lower()

    if "no " in ref and ("conflict" in ref or "safety" in ref or "risk" in ref):
        severe = [f for f in flags if f.get("severity") in ("high", "critical")]
        if not severe:
            return "match", "no high/critical flags"
        return "mismatch", f"expected no risk but got {len(severe)} high/critical flags"

    if not ref or ref == "nan":
        return "unknown", "no reference risk provided"

    if flags:
        return "match", f"{len(flags)} risk flag(s) present"
    return "mismatch", f"expected risk flags but got none"


def evaluate_csv(csv_path: Path) -> dict:
    if not csv_path.exists():
        return None

    df = pd.read_csv(csv_path)
    total = len(df)
    successful = df[df["pipeline_error"].fillna("").astype(str).str.strip() == ""]

    rows = []
    for _, r in successful.iterrows():
        actions = _parse_actions(r.get("actual_clinical_actions", ""))
        flags = _parse_risk_flags(r.get("actual_risk_flags", ""))

        action_verdict, action_detail = classify_action_match(
            r["reference_expected_action"], actions, deferred=bool(r.get("actual_deferred")), status=str(r.get("actual_status", ""))
        )
        risk_verdict, risk_detail = classify_risk_match(r["reference_expected_risk"], flags)

        overall = "match" if action_verdict == "match" and risk_verdict == "match" else (
            "partial" if "match" in (action_verdict, risk_verdict) else "mismatch"
        )
        rows.append(overall)

    counts = Counter(rows)
    return {
        "total": total,
        "match": counts.get("match", 0),
        "partial": counts.get("partial", 0),
        "mismatch": counts.get("mismatch", 0),
        "unknown": counts.get("unknown", 0),
    }


def main():
    mode_csvs = {
        "rules_only": RESULTS_DIR / "caretrace_1000_agent_answers_rules_only.csv",
        "llm_only": RESULTS_DIR / "caretrace_1000_agent_answers_llm_only.csv",
        "hybrid": RESULTS_DIR / "caretrace_1000_agent_answers_hybrid.csv",
    }

    if not mode_csvs["rules_only"].exists() and RESULTS_FILE.exists():
        mode_csvs["rules_only"] = RESULTS_FILE

    mode_evals = {}
    for mode, path in mode_csvs.items():
        res = evaluate_csv(path)
        if res:
            mode_evals[mode] = res

    # Print clean Markdown / ASCII summary table
    print("\n" + "=" * 65)
    print("1,000-PATIENT BENCHMARK EVALUATION SUMMARY TABLE")
    print("=" * 65)
    print(f"| {'Mode':<12} | {'Match':<8} | {'Partial':<8} | {'Mismatch':<8} | {'Total':<6} |")
    print(f"|{'-'*14}|{'-'*10}|{'-'*10}|{'-'*10}|{'-'*8}|")

    for mode in ["rules_only", "llm_only", "hybrid"]:
        if mode in mode_evals:
            e = mode_evals[mode]
            print(f"| {mode:<12} | {e['match']:<8} | {e['partial']:<8} | {e['mismatch']:<8} | {e['total']:<6} |")
        else:
            print(f"| {mode:<12} | {'(pending)':<8} | {'(pending)':<8} | {'(pending)':<8} | {'N/A':<6} |")

    print("=" * 65 + "\n")


if __name__ == "__main__":
    main()
