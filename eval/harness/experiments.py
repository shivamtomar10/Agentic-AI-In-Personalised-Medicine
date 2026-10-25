"""Experiments. Every number is computed from real pipeline runs (no simulated probabilities).

Modes compared: FULL_RERUN baseline (same agents, no memory of prior reasoning) vs SELECTIVE_REPLAN.
"""
from __future__ import annotations

import random
from dataclasses import replace
from statistics import mean, pstdev
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from eval.faults import FAULT_MATRIX, FaultInjector, FaultSpec
from eval.harness.cohort import simulate_patient
from eval.harness.perturb import PERTURBATIONS, perturb
from eval.harness.scenarios import build_pair, load_adaptation
from eval.metrics import (FaultOutcome, aurc, breakdown, check_expectations, compute_pcr, compute_rsi, compute_ucr, recommendation_signature,
                          risk_coverage_curve)
from src.contracts.agent_io import ExecutionMode
from src.ingest.builders import build_state
from src.orchestrator import PipelineConfig, PipelineRunner
from src.state import DeltaEngine

FULL, SEL = ExecutionMode.FULL_RERUN, ExecutionMode.SELECTIVE_REPLAN
CONFIGS: Dict[str, Dict[str, Any]] = {
    "full_system": {}, "no_provenance_gate": {"enable_provenance_gate": False}, "no_validation": {"enable_validation": False},
    "no_abstention": {"enable_abstention": False}, "no_safety_replan": {"enable_safety_replan": False},
    "no_defences": {"enable_provenance_gate": False, "enable_validation": False, "enable_abstention": False},
}
_RUNNERS: Dict[str, PipelineRunner] = {}


def runner(name: str = "full_system") -> PipelineRunner:
    if name not in _RUNNERS:
        _RUNNERS[name] = PipelineRunner(PipelineConfig(**CONFIGS[name]))
    return _RUNNERS[name]


def ms(xs: Sequence[float]) -> Dict[str, Optional[float]]:
    xs = [x for x in xs if x is not None]
    return {"mean": round(mean(xs), 4) if xs else None, "sd": round(pstdev(xs), 4) if len(xs) > 1 else 0.0, "n": len(xs)}


def _run_mode(rn: PipelineRunner, mode: ExecutionMode, prev, curr, prev_res=None):
    """Baseline sees only the current snapshot; selective additionally gets the prior run's cached reasoning."""
    if mode == FULL:
        return rn.run(curr, prev=prev, mode=FULL)
    return rn.run(curr, prev=prev, prev_run=prev_res, mode=SEL)


# ----------------------------------------------------------------------------- adaptation
def adaptation(seeds: Sequence[int], cfg: str = "full_system", suite: str = "core") -> Dict[str, Any]:
    scs, _ = load_adaptation(suite)
    rn = runner(cfg)
    out: Dict[str, Any] = {"per_mode": {}, "per_family": {}, "failures": []}
    claims_all = []
    for mode in (FULL, SEL):
        per_seed = []
        for sd in seeds:
            oks = []
            for s in scs:
                p, c = build_pair(s, sd)
                r1 = rn.run(p, mode=FULL) if mode == SEL else None
                res = _run_mode(rn, mode, p, c, r1)
                ok, miss = check_expectations(res.recommendation, s["expect"])
                oks.append(ok)
                out["per_family"].setdefault(s["id"], {}).setdefault(mode.value, []).append(ok)
                if not ok:
                    out["failures"].append({"scenario": s["id"], "mode": mode.value, "seed": sd, "misses": miss})
                claims_all += [cl for o in res.outputs.values() for cl in o.claims] if mode == SEL else []
            per_seed.append(sum(oks) / len(oks))
        out["per_mode"][mode.value] = ms(per_seed)
    out["ucr_raw"] = compute_ucr(claims_all)
    return out


# ----------------------------------------------------------------------------- stability (RSI)
def stability(seeds: Sequence[int], cfg: str = "full_system") -> Dict[str, Any]:
    scs, neg = load_adaptation()
    rn = runner(cfg)
    res: Dict[str, Dict[str, list]] = {m.value: {k: [] for k in PERTURBATIONS} for m in (FULL, SEL)}
    for sd in seeds:
        rng = random.Random(sd)
        for s in scs + [neg]:
            p, c = build_pair(s, sd)
            r_prev = rn.run(p, mode=FULL)
            base0 = rn.run(c, prev=p, mode=FULL)
            sel0 = rn.run(c, prev=p, prev_run=r_prev, mode=SEL)
            for k in PERTURBATIONS:
                pert = perturb(c, k, rng, 3)
                res[FULL.value][k].append((base0.recommendation, rn.run(pert, prev=c, mode=FULL).recommendation))
                res[SEL.value][k].append((sel0.recommendation, rn.run(pert, prev=c, prev_run=sel0, mode=SEL).recommendation))
    return {"per_mode": {m: {"overall": compute_rsi([x for v in d.values() for x in v]), "by_perturbation": {k: compute_rsi(v) for k, v in d.items()}} for m, d in res.items()}}


def boundaries(cfg: str = "full_system") -> Dict[str, Any]:
    """Threshold flicker: value straddling a decision threshold by +-0.4 (inside assay noise). safe = moves to the safe side."""
    rn = runner(cfg)
    cases = [("eGFR", 29.6, 30.4, "metformin_30"), ("eGFR", 44.6, 45.4, "metformin_45"), ("eGFR", 59.6, 60.4, "ckd_60"), ("Potassium", 5.6, 5.4, "k_5.5"), ("HbA1c", 7.1, 6.9, "a1c_7")]
    rows = []
    for an, unsafe_v, safe_v, tag in cases:
        for direction, v0, v1 in (("to_safe", unsafe_v, safe_v), ("to_unsafe", safe_v, unsafe_v)):
            def mk(v, ver):
                labs = {"eGFR": 70, "Potassium": 4.4, "HbA1c": 6.6, "ALT": 28}
                labs[an] = v
                return build_state("BND", "1975-01-01", "male", timestamp=f"2026-0{ver}-01", version=ver, conditions=["Type 2 diabetes mellitus (E11.9)", "Hypertension (I10)"],
                                   meds=["Metformin 1000 mg BID", "Lisinopril 20 mg QD"], labs=labs, vitals={"SBP": 124})
            s0, s1 = mk(v0, 1), mk(v1, 2)
            b0, b1 = rn.run(s0, mode=FULL), rn.run(s1, prev=s0, mode=FULL)
            r0 = rn.run(s0, mode=FULL)
            x1 = rn.run(s1, prev=s0, prev_run=r0, mode=SEL)
            rows.append({"case": tag, "direction": direction, "baseline_changed": recommendation_signature(b0.recommendation) != recommendation_signature(b1.recommendation),
                         "selective_changed": recommendation_signature(r0.recommendation) != recommendation_signature(x1.recommendation)})
    def rate(d, k, want): 
        xs = [r for r in rows if r["direction"] == d]
        return sum(r[k] == want for r in xs) / len(xs)
    return {"rows": rows,
            "to_safe_stability": {"baseline": rate("to_safe", "baseline_changed", False), "selective": rate("to_safe", "selective_changed", False)},
            "to_unsafe_responsiveness": {"baseline": rate("to_unsafe", "baseline_changed", True), "selective": rate("to_unsafe", "selective_changed", True)}}


# ----------------------------------------------------------------------------- faults (PCR)
def _rich(seed: int):
    r = random.Random(seed)
    return build_state(f"FLT-{seed}", "1952-04-01", "female", timestamp="2026-03-01", version=1, conditions=["Type 2 diabetes mellitus (E11.9)", "Hypertension (I10)"],
                       meds=["Metformin 1000 mg BID", "Lisinopril 20 mg QD", "Warfarin 5 mg QD", "Ibuprofen 400 mg TID", "Amoxicillin 500 mg TID", "Ondansetron 4 mg TID", "Azithromycin 250 mg QD"],
                       allergies=["Penicillin (rash)"], labs={"eGFR": round(r.uniform(16, 28), 1), "Potassium": round(r.uniform(5.6, 6.1), 1), "HbA1c": round(r.uniform(8.6, 10.2), 1), "Creatinine": round(r.uniform(2.2, 3.0), 2), "ALT": 30, "INR": 2.4},
                       vitals={"SBP": r.randint(140, 155)})


def _outcome(rec, spec: FaultSpec, kind: str, point: str) -> FaultOutcome:
    ids = set(spec.claim_ids)
    sup = {c.claim_id for c in rec.supported_claims}
    approved_refs = {i for a in rec.clinical_actions for i in a.claim_ids} | {f.claim_id for f in rec.risk_flags}
    vr = rec.validation_report
    detected = bool(vr and vr.fault_detected) or bool(ids & {c.claim_id for c in rec.flagged_claims}) or rec.deferred
    if kind == "dropped_allergy":
        contained = rec.deferred or any(f.category == "allergy" for f in rec.risk_flags)
    else:
        contained = not (ids & sup) and not (ids & approved_refs)
    if not spec.applied:
        contained, detected = True, False  # fault could not be applied (no target) -> excluded upstream
    return FaultOutcome(kind, point, detected, contained)


def faults(seeds: Sequence[int], cfg: str = "full_system") -> Dict[str, Any]:
    rn = runner(cfg)
    outs: List[FaultOutcome] = []
    claims = []
    approved = []
    for sd in seeds:
        for kind, points in FAULT_MATRIX.items():
            for pt in points:
                spec = FaultSpec(kind, pt, sd)
                res = PipelineRunner(PipelineConfig(**{**CONFIGS[cfg], "fault_hook": FaultInjector(spec)}), retriever=rn.retriever).run(_rich(sd), mode=FULL)
                if not spec.applied:
                    continue
                outs.append(_outcome(res.recommendation, spec, kind, pt))
                claims += [c for o in res.outputs.values() for c in o.claims]
                approved += res.recommendation.supported_claims
    ok_claims = [c for c in claims]
    return {"n_injected": len(outs), "pcr": compute_pcr(outs), "detection_rate": sum(o.detected for o in outs) / len(outs) if outs else None,
            "breakdown": breakdown(outs), "ucr_raw": compute_ucr(ok_claims), "ucr_approved": compute_ucr(approved),
            "escaped": [{"kind": o.kind, "point": o.point} for o in outs if not o.contained]}


# ----------------------------------------------------------------------------- cohort: efficiency + engine quality
def cohort(seeds: Sequence[int], n_patients: int, n_enc: int, p_signal: float, cfg: str = "full_system") -> Dict[str, Any]:
    rn = runner(cfg)
    eng = DeltaEngine()
    inv = {FULL.value: [], SEL.value: []}
    reasoning = {FULL.value: [], SEL.value: []}
    lat = {FULL.value: [], SEL.value: []}
    agree, div, tp, fn, fp, tn, replans = 0, 0, 0, 0, 0, 0, 0
    sig_by_size = []
    for sd in seeds:
        for i in range(n_patients):
            enc = simulate_patient(sd, i, n_enc, p_signal)
            prev_res = None
            prev_state = None
            for e in enc:
                b = rn.run(e.state, prev=prev_state, mode=FULL)
                s = rn.run(e.state, prev=prev_state, prev_run=prev_res, mode=SEL)
                if prev_state is not None:
                    inv[FULL.value].append(b.recommendation.metrics.agent_invocations); inv[SEL.value].append(s.recommendation.metrics.agent_invocations)
                    reasoning[FULL.value].append(b.recommendation.metrics.reasoning_invocations); reasoning[SEL.value].append(s.recommendation.metrics.reasoning_invocations)
                    lat[FULL.value].append(b.recommendation.metrics.wall_clock_ms); lat[SEL.value].append(s.recommendation.metrics.wall_clock_ms)
                    same = recommendation_signature(b.recommendation) == recommendation_signature(s.recommendation)
                    agree += same; div += (not same); replans += s.recommendation.metrics.safety_replans
                    flagged = s.delta_report.is_clinically_significant
                    tp += e.is_signal and flagged; fn += e.is_signal and not flagged; fp += (not e.is_signal) and flagged; tn += (not e.is_signal) and not flagged
                    sig_by_size.append(e.is_signal)
                prev_state, prev_res = e.state, s
    n = agree + div
    fb, fs = mean(inv[FULL.value]), mean(inv[SEL.value])
    return {"p_signal": p_signal, "n_encounters": n, "signal_fraction": round(sum(sig_by_size) / len(sig_by_size), 4),
            "invocations_per_encounter": {"full_rerun": round(fb, 3), "selective": round(fs, 3)}, "invocation_reduction": round(1 - fs / fb, 4),
            "reasoning_per_encounter": {"full_rerun": round(mean(reasoning[FULL.value]), 3), "selective": round(mean(reasoning[SEL.value]), 3)},
            "reasoning_reduction": round(1 - mean(reasoning[SEL.value]) / mean(reasoning[FULL.value]), 4),
            "latency_ms": {"full_rerun": round(mean(lat[FULL.value]), 2), "selective": round(mean(lat[SEL.value]), 2)},
            "selective_vs_full_agreement": round(agree / n, 4), "safety_replans": replans,
            "delta_engine": {"recall_signal": round(tp / (tp + fn), 4) if tp + fn else None, "false_positive_rate": round(fp / (fp + tn), 4) if fp + tn else None, "tp": tp, "fn": fn, "fp": fp, "tn": tn}}


def threshold_sweep(seeds: Sequence[int], n_patients: int, n_enc: int, p_signal: float = 0.2) -> List[Dict[str, float]]:
    rows = []
    reports = []
    for sd in seeds:
        for i in range(n_patients):
            enc = simulate_patient(sd, i, n_enc, p_signal)
            eng = DeltaEngine()
            for a, b in zip(enc, enc[1:]):
                reports.append((b.is_signal, eng.compute(a.state, b.state).max_significance))
    for t in [0.05, 0.15, 0.25, 0.3, 0.35, 0.45, 0.55, 0.65, 0.75, 0.85, 0.95]:
        sig = [m for s, m in reports if s]; non = [m for s, m in reports if not s]
        rows.append({"threshold": t, "recall_signal": round(sum(m >= t for m in sig) / len(sig), 4), "noise_suppression": round(sum(m < t for m in non) / len(non), 4)})
    return rows


# ----------------------------------------------------------------------------- selective prediction
def selective_prediction(seeds: Sequence[int]) -> Dict[str, Any]:
    scs, _ = load_adaptation()
    conf, corr, kinds = [], [], []
    rn_off = runner("no_defences")
    for sd in seeds:
        for s in scs:
            p, c = build_pair(s, sd)
            base = rn_off.run(c, prev=p, mode=FULL).recommendation
            ok, _ = check_expectations(base, s["expect"])
            conf.append(base.aggregate_confidence); corr.append(ok); kinds.append("normal")
            d = c.model_copy(update={"latest_labs": {k: v for k, v in c.latest_labs.items() if k not in ("eGFR", "Potassium", "HbA1c")}})
            r = rn_off.run(d, prev=p, mode=FULL).recommendation
            ok, _ = check_expectations(r, s["expect"]); conf.append(r.aggregate_confidence); corr.append(ok); kinds.append("missing_labs")
            for kind, pt in (("inverted_lab_value", "patient_data"), ("dropped_allergy", "patient_data"), ("fabricated_interaction", "risk_assessment")):
                spec = FaultSpec(kind, pt, sd)
                r = PipelineRunner(PipelineConfig(enable_provenance_gate=False, enable_validation=False, enable_abstention=False, fault_hook=FaultInjector(spec)), retriever=rn_off.retriever).run(c, prev=p, mode=FULL).recommendation
                ok, _ = check_expectations(r, s["expect"]); ok = ok and not (spec.applied and kind == "fabricated_interaction")
                conf.append(r.aggregate_confidence); corr.append(ok); kinds.append("fault_no_validation")
    curve = risk_coverage_curve(conf, corr)
    return {"n_cases": len(conf), "aurc": round(aurc(conf, corr), 4), "overall_risk": round(sum(not c for c in corr) / len(corr), 4),
            "curve": [{"threshold": t, "coverage": round(cv, 4), "risk": round(rk, 4)} for t, cv, rk in curve],
            "mean_confidence": {"correct": round(mean([c for c, k in zip(conf, corr) if k]), 4), "incorrect": round(mean([c for c, k in zip(conf, corr) if not k]), 4) if not all(corr) else None}}
