"""Fit + evaluate abstention calibration on labelled synthetic cases.

Cases (per scenario, per seed): normal | missing_labs | unknown_drug (known drugs swapped for drugs outside the table) |
3 injected faults with validation/gate OFF (so errors can reach the output). Label = does the recommendation satisfy the
scenario's ground truth. Train seeds and test seeds are DISJOINT; the reported numbers are out-of-sample.

    python -m eval.calibrate_abstention           # evaluate only
    python -m eval.calibrate_abstention --write   # also write config/abstention_weights.yaml (mode: logistic)
"""
from __future__ import annotations

import argparse
import re
from statistics import mean
from typing import Any, Dict, List, Sequence

from eval.faults import FaultInjector, FaultSpec
from eval.harness.scenarios import build_pair, load_adaptation
from eval.metrics import aurc, check_expectations
from src.contracts.agent_io import ExecutionMode
from src.orchestrator import PipelineConfig, PipelineRunner
from src.safety.calibration import FEATURES, LogisticCalibrator, fit_logistic, save_calibrator

FULL = ExecutionMode.FULL_RERUN
UNKNOWN = {"warfarin": "Acenocoumarol", "ibuprofen": "Dexketoprofen", "naproxen": "Etoricoxib", "lisinopril": "Perindopril", "losartan": "Irbesartan-x", "atorvastatin": "Pitavastatin",
           "simvastatin": "Pitavastatin", "azithromycin": "Roxithromycin", "ondansetron": "Granisetron", "citalopram": "Sertraline", "amiodarone": "Dronedarone",
           "lorazepam": "Bromazepam", "diphenhydramine": "Dimenhydrinate", "cefuroxime": "Cefpodoxime", "apixaban": "Edoxaban", "metformin": "Gliclazide-mr"}


def _swap(meds_specs):
    return [re.sub(r"^\s*([A-Za-z\-]+)", lambda m: UNKNOWN.get(m.group(1).lower(), m.group(1)), s) for s in meds_specs]


def _cases(seed: int):
    scs = load_adaptation("core")[0] + load_adaptation("holdout")[0]
    for s in scs:
        p, c = build_pair(s, seed)
        yield s, "normal", p, c
        yield s, "missing_labs", p, c.model_copy(update={"latest_labs": {k: v for k, v in c.latest_labs.items() if k in ("Sodium", "Hemoglobin", "Creatinine")}})
        s2 = {**s, "prev": {**s["prev"], "meds": _swap(s["prev"]["meds"])}, "curr": {**s["curr"], "meds": _swap(s["curr"]["meds"])}}
        p2, c2 = build_pair(s2, seed)
        yield s, "unknown_drug", p2, c2
        for kind, pt in (("inverted_lab_value", "patient_data"), ("dropped_allergy", "patient_data"), ("fabricated_interaction", "risk_assessment")):
            yield s, f"fault:{kind}", p, c


def collect(seeds: Sequence[int]) -> List[Dict[str, Any]]:
    rn = PipelineRunner(PipelineConfig(enable_abstention=False))
    rows = []
    for sd in seeds:
        for s, kind, p, c in _cases(sd):
            if kind.startswith("fault:"):
                spec = FaultSpec(kind.split(":")[1], "patient_data" if "dropped" in kind or "inverted" in kind else "risk_assessment", sd)
                r = PipelineRunner(PipelineConfig(enable_abstention=False, enable_validation=False, enable_provenance_gate=False, fault_hook=FaultInjector(spec)), retriever=rn.retriever).run(c, prev=p, mode=FULL).recommendation
                if not spec.applied:
                    continue
            else:
                r = rn.run(c, prev=p, mode=FULL).recommendation
            ok, _ = check_expectations(r, s["expect"])
            f = r.confidence_components
            rows.append({"features": f, "fixed": r.aggregate_confidence, "correct": ok, "kind": kind.split(":")[0]})
    return rows


def auroc(scores: Sequence[float], y: Sequence[bool]) -> float:
    pos = [s for s, k in zip(scores, y) if k]; neg = [s for s, k in zip(scores, y) if not k]
    if not pos or not neg:
        return float("nan")
    return sum((p > n) + 0.5 * (p == n) for p in pos for n in neg) / (len(pos) * len(neg))


def evaluate(train_seeds=(100, 101, 102), test_seeds=(200, 201, 202)) -> Dict[str, Any]:
    tr, te = collect(train_seeds), collect(test_seeds)
    cal = fit_logistic([r["features"] for r in tr], [r["correct"] for r in tr])
    fx = [r["fixed"] for r in te]; ln = [cal.predict(r["features"]) for r in te]; hy = [min(a, b) for a, b in zip(fx, ln)]; y = [r["correct"] for r in te]
    m = lambda xs, k: round(mean([x for x, kk in zip(xs, y) if kk == k]), 4) if any(kk == k for kk in y) else None
    by_kind = {}
    for r in te:
        d = by_kind.setdefault(r["kind"], [0, 0]); d[0] += 1; d[1] += r["correct"]
    return {"train_cases": len(tr), "test_cases": len(te), "test_error_rate": round(1 - sum(y) / len(y), 4),
            "fixed": {"aurc": round(aurc(fx, y), 4), "auroc": round(auroc(fx, y), 4), "mean_conf_correct": m(fx, True), "mean_conf_incorrect": m(fx, False)},
            "learned": {"aurc": round(aurc(ln, y), 4), "auroc": round(auroc(ln, y), 4), "mean_conf_correct": m(ln, True), "mean_conf_incorrect": m(ln, False)},
            "hybrid_min": {"aurc": round(aurc(hy, y), 4), "auroc": round(auroc(hy, y), 4)},
            "accuracy_by_case_kind": {k: {"n": v[0], "correct": round(v[1] / v[0], 3)} for k, v in by_kind.items()},
            "weights": dict(zip(cal.features, [round(w, 3) for w in cal.weights])), "calibrator": cal.to_dict(), "train_seeds": list(train_seeds), "test_seeds": list(test_seeds)}


if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--write", action="store_true"); a = ap.parse_args()
    R = evaluate()
    print({k: v for k, v in R.items() if k != "calibrator"})
    if a.write:
        save_calibrator(LogisticCalibrator(**{k: R["calibrator"][k] for k in ("weights", "bias", "mean", "std", "features")}), note=f"train seeds {R['train_seeds']}; out-of-sample AURC fixed {R['fixed']['aurc']} vs learned {R['learned']['aurc']}")
        print("wrote config/abstention_weights.yaml")
