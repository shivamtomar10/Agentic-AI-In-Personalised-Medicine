"""Run ALL experiments and write eval/results/results.json, docs/results.md and figures.

    python -m eval.harness.run_evaluation            # default: 3 seeds
    python -m eval.harness.run_evaluation --quick    # fast smoke run (1 seed, small cohort)
"""
from __future__ import annotations

import argparse
import json
import platform
import sys
import time
from datetime import datetime, timezone

from eval import calibrate_abstention
from eval.harness import experiments as X, linker_eval
from eval.harness.report import write_report
from src.config import PROJECT_ROOT, get_settings


def run_all(seeds, quick: bool) -> dict:
    t0 = time.time()
    n_pat, n_enc = (10, 6) if quick else (30, 10)
    R: dict = {"meta": {"seeds": list(seeds), "quick": quick, "cohort": {"patients_per_seed": n_pat, "encounters_per_patient": n_enc},
                        "python": platform.python_version(), "created_utc": datetime.now(timezone.utc).isoformat(),
                        "data": "SYNTHETIC only (author-written scenarios + seeded simulator; not Synthea/MIMIC)",
                        "agents": "deterministic rule+retrieval cores (no LLM); see docs/limitations.md"}}
    print("adaptation ..."); R["adaptation"] = X.adaptation(seeds)
    print("held-out adaptation ..."); R["adaptation_holdout"] = X.adaptation(seeds, suite="holdout")
    print("evidence linker ..."); R["linker"] = linker_eval.evaluate(); R["linker_strict"] = linker_eval.evaluate(strict=True)
    print("abstention calibration ..."); R["calibration"] = calibrate_abstention.evaluate()
    print("stability ..."); R["stability"] = X.stability(seeds)
    print("boundaries ..."); R["boundaries"] = X.boundaries()
    print("faults ..."); R["faults"] = X.faults(seeds)
    R["faults_no_defences"] = X.faults(seeds, "no_defences")
    print("cohort/efficiency ...")
    R["efficiency_sweep"] = [X.cohort(seeds, n_pat, n_enc, p) for p in (0.05, 0.10, 0.20, 0.40)]
    print("threshold sweep ..."); R["threshold_sweep"] = X.threshold_sweep(seeds, n_pat, n_enc)
    print("selective prediction ..."); R["selective_prediction"] = X.selective_prediction(seeds)
    print("ablations ...")
    ab = {}
    for name in X.CONFIGS:
        a = X.adaptation(seeds, name)
        f = X.faults(seeds, name)
        c = X.cohort(seeds[:1], max(6, n_pat // 2), n_enc, 0.2, name)
        ab[name] = {"adaptation_recall_selective": a["per_mode"]["selective_replan"]["mean"], "pcr": f["pcr"], "detection_rate": f["detection_rate"],
                    "ucr_approved": f["ucr_approved"], "selective_vs_full_agreement": c["selective_vs_full_agreement"], "safety_replans": c["safety_replans"],
                    "invocation_reduction": c["invocation_reduction"]}
    R["ablations"] = ab
    R["meta"]["runtime_s"] = round(time.time() - t0, 1)
    return R


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--seeds", type=int, nargs="+", default=None)
    a = ap.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    seeds = a.seeds or ([0] if a.quick else [0, 1, 2])
    R = run_all(seeds, a.quick)
    out = get_settings().results_dir
    out.mkdir(parents=True, exist_ok=True)
    (out / "results.json").write_text(json.dumps(R, indent=2, default=str), encoding="utf-8")
    write_report(R, out, PROJECT_ROOT / "docs" / "results.md")
    print(f"done in {R['meta']['runtime_s']}s -> {out / 'results.json'}")


if __name__ == "__main__":
    main()
