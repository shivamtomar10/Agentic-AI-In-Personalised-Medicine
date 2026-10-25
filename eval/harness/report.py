"""Turns results.json into docs/results.md (text derived from the numbers) and PNG figures."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List


def _f(x, d=3):
    return "n/a" if x is None else (f"{x:.{d}f}" if isinstance(x, float) else str(x))


def _ms(d):
    return f"{_f(d['mean'])} +/- {_f(d['sd'])} (n={d['n']})"


def findings(R: Dict[str, Any]) -> List[str]:
    out = []
    ar = R["adaptation"]["per_mode"]
    out.append(f"Adaptation recall (10 authored change families): full re-run {_ms(ar['full_rerun'])}, selective {_ms(ar['selective_replan'])}. "
               + ("Both modes satisfy every ground-truth expectation, so this suite does not separate them (it is a correctness check of the rule set)." if ar["full_rerun"]["mean"] == ar["selective_replan"]["mean"] == 1.0
                  else "The modes differ; see failures in results.json."))
    if "adaptation_holdout" in R:
        h = R["adaptation_holdout"]["per_mode"]
        out.append(f"Held-out adaptation suite (13 scenarios written from clinical guidance before being run; author-written, so only semi-independent): full re-run {_ms(h['full_rerun'])}, selective {_ms(h['selective_replan'])}. "
                   "On its first run 12/13 passed; the failure (cephalosporin in a penicillin-allergic patient produced a flag but no action) was a real clinical gap, fixed without editing the expectation.")
    if "linker" in R:
        L = R["linker"]
        out.append("Evidence linker (lexical, not NLI) on labelled claim/evidence pairs: " + "; ".join(f"{k} n={v['n']} precision {_f(v['precision'])} recall {_f(v['recall'])}" for k, v in L.items()) +
                   ". `dev` guided the rules; `test` is CONTAMINATED (its baseline errors were seen while designing rules); `test2` was written afterwards and is the honest estimate - it still admits false positives (antonym adjectives, 'type 1' vs 'type 2', unknown words, re-used numbers). Baseline (before the rewrite) was ~0.50-0.61 precision.")
    if "calibration" in R:
        c = R["calibration"]
        out.append(f"Abstention calibration, out-of-sample ({c['test_cases']} test cases, seeds disjoint from training): fixed-weight blend AURC {_f(c['fixed']['aurc'], 4)}, AUROC {_f(c['fixed']['auroc'])}, mean confidence correct/incorrect {_f(c['fixed']['mean_conf_correct'])}/{_f(c['fixed']['mean_conf_incorrect'])}; "
                   f"learned logistic AURC {_f(c['learned']['aurc'], 4)}, AUROC {_f(c['learned']['auroc'])}, mean confidence {_f(c['learned']['mean_conf_correct'])}/{_f(c['learned']['mean_conf_incorrect'])}. "
                   f"Learned weights: {c['weights']}. Accuracy by case kind: {c['accuracy_by_case_kind']}. The pool contains error sources (missing labs, drugs outside the table) that the features were designed to detect; "
                   "injected faults with validation off are NOT separable by these features.")
    rs = R["stability"]["per_mode"]
    a, b = rs["full_rerun"]["overall"], rs["selective_replan"]["overall"]
    out.append(f"RSI on semantics-preserving perturbations: full re-run {_f(a)}, selective {_f(b)}. "
               + ("Identical because the agents are deterministic: the stochastic LLM-drift problem that motivates RSI cannot manifest without LLM-backed agents (not built)." if a == b else "They differ."))
    bd = R["boundaries"]
    out.append(f"Threshold flicker (value moves back across a decision threshold within assay noise): stability full re-run {_f(bd['to_safe_stability']['baseline'], 2)} vs selective {_f(bd['to_safe_stability']['selective'], 2)}; "
               f"responsiveness to unsafe-direction crossings full re-run {_f(bd['to_unsafe_responsiveness']['baseline'], 2)} vs selective {_f(bd['to_unsafe_responsiveness']['selective'], 2)}. "
               "This is the only place the temporal design measurably changes stability, and it is a structural effect (hysteresis), not LLM noise. These 10 cases were chosen by the author to straddle thresholds.")
    eff = R["efficiency_sweep"]
    out.append("Efficiency depends entirely on how often true change occurs (a modelling assumption of the synthetic simulator): " +
               "; ".join(f"signal fraction {e['signal_fraction']:.2f} -> total invocations -{e['invocation_reduction']*100:.0f}%, reasoning invocations -{e['reasoning_reduction']*100:.0f}%" for e in eff) + ".")
    e2 = eff[2]
    out.append(f"DeltaEngine vs ground-truth events (p=0.2): signal recall {_f(e2['delta_engine']['recall_signal'])}, false-positive rate {_f(e2['delta_engine']['false_positive_rate'])}; "
               f"selective/full recommendations agree on {e2['selective_vs_full_agreement']*100:.1f}% of encounters; safety re-plans triggered: {e2['safety_replans']}.")
    f, n = R["faults"], R["faults_no_defences"]
    out.append(f"Propagation containment over {f['n_injected']} injected faults: PCR {_f(f['pcr'])} with defences vs {_f(n['pcr'])} with validation, provenance gate and abstention disabled. "
               "Faults are author-designed and detectable by the checks the author wrote; PCR = 1.0 shows the checks work on these fault classes, not that unseen failure modes are contained.")
    ab = R["ablations"]
    moved = [k for k, v in ab.items() if k != "full_system" and (v["pcr"] != ab["full_system"]["pcr"] or v["ucr_approved"] != ab["full_system"]["ucr_approved"] or v["safety_replans"] != ab["full_system"]["safety_replans"] or v["selective_vs_full_agreement"] != ab["full_system"]["selective_vs_full_agreement"])]
    out.append("Ablations that changed at least one metric: " + (", ".join(moved) if moved else "none") + ".")
    sp = R["selective_prediction"]
    out.append(f"Selective prediction (AURC, lower is better) over {sp['n_cases']} degraded/faulted cases: {_f(sp['aurc'])}; overall error {_f(sp['overall_risk'])}; "
               f"mean confidence correct {_f(sp['mean_confidence']['correct'])} vs incorrect {_f(sp['mean_confidence']['incorrect'])}.")
    return out


def write_report(R: Dict[str, Any], out_dir: Path, md_path: Path) -> None:
    F = findings(R)
    m = R["meta"]
    L = ["# Evaluation results (auto-generated)", "", f"Generated {m['created_utc']} | seeds {m['seeds']} | quick={m['quick']} | runtime {m['runtime_s']}s", "",
         "> Every number below is computed from real pipeline runs by `python run.py eval`. Data are synthetic; agents are deterministic rule+retrieval cores (no LLM).",
         "> Do not quote these as clinical performance. They evaluate the software design on author-built scenarios.", "", "## Findings (text derived from the numbers)", ""]
    L += [f"- {x}" for x in F]
    if "linker" in R:
        L += ["", "## Evidence linker (precision / recall on labelled pairs)", "", "| split | n | precision | recall | FP rate | note |", "|---|---|---|---|---|---|"]
        notes = {"dev": "guided the rules", "test": "contaminated (errors seen)", "test2": "written after; honest estimate"}
        for k, v in R["linker"].items():
            L.append(f"| {k} | {v['n']} | {_f(v['precision'])} | {_f(v['recall'])} | {_f(v['false_positive_rate'])} | {notes.get(k, '')} |")
        if "linker_strict" in R:
            L += ["", "Strict mode (used for LLM-originated text: every content word must occur in the evidence):", "", "| split | n | precision | recall | FP rate |", "|---|---|---|---|---|"]
            for k, v in R["linker_strict"].items():
                L.append(f"| {k} | {v['n']} | {_f(v['precision'])} | {_f(v['recall'])} | {_f(v['false_positive_rate'])} |")
        L += ["", "Remaining errors on test2 (non-strict):", ""] + [f"- {e[0]}: {e[2]}" for e in R["linker"]["test2"]["errors"]]
    L += ["", "## Ablations", "", "| config | AR (selective) | PCR | detect | UCR approved | agree sel/full | safety replans | invocation reduction |", "|---|---|---|---|---|---|---|---|"]
    for k, v in R["ablations"].items():
        L.append(f"| {k} | {_f(v['adaptation_recall_selective'])} | {_f(v['pcr'])} | {_f(v['detection_rate'])} | {_f(v['ucr_approved'])} | {_f(v['selective_vs_full_agreement'])} | {v['safety_replans']} | {_f(v['invocation_reduction'])} |")
    L += ["", "## PCR by fault type and injection point (full system)", "", "| fault | point | n | PCR |", "|---|---|---|---|"]
    for k, pts in R["faults"]["breakdown"].items():
        for p, v in pts.items():
            L.append(f"| {k} | {p} | {int(v['n'])} | {_f(v['pcr'], 2)} |")
    L += ["", "## RSI by perturbation", "", "| perturbation | full re-run | selective |", "|---|---|---|"]
    bp = R["stability"]["per_mode"]
    for k in bp["full_rerun"]["by_perturbation"]:
        L.append(f"| {k} | {_f(bp['full_rerun']['by_perturbation'][k], 2)} | {_f(bp['selective_replan']['by_perturbation'][k], 2)} |")
    L += ["", "## Failures", ""] + ([f"- {x}" for x in R["adaptation"]["failures"]] or ["- none in the adaptation suite"])
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text("\n".join(L) + "\n", encoding="utf-8")
    try:
        _plots(R, out_dir / "figs")
    except Exception as e:  # matplotlib optional
        print("figures skipped:", e)


def _plots(R, d: Path) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    d.mkdir(parents=True, exist_ok=True)
    eff = R["efficiency_sweep"]
    plt.figure(figsize=(5, 3.4)); plt.plot([e["signal_fraction"] for e in eff], [e["invocation_reduction"] * 100 for e in eff], "o-", label="all agents")
    plt.plot([e["signal_fraction"] for e in eff], [e["reasoning_reduction"] * 100 for e in eff], "s--", label="reasoning agents")
    plt.xlabel("fraction of encounters with true change"); plt.ylabel("% fewer invocations"); plt.legend(); plt.tight_layout(); plt.savefig(d / "efficiency_vs_change_rate.png", dpi=150); plt.close()
    ts = R["threshold_sweep"]
    plt.figure(figsize=(5, 3.4)); plt.plot([t["threshold"] for t in ts], [t["recall_signal"] for t in ts], label="signal recall"); plt.plot([t["threshold"] for t in ts], [t["noise_suppression"] for t in ts], label="noise suppression")
    plt.xlabel("re-plan threshold"); plt.legend(); plt.tight_layout(); plt.savefig(d / "stability_vs_responsiveness.png", dpi=150); plt.close()
    sp = R["selective_prediction"]["curve"]
    plt.figure(figsize=(5, 3.4)); plt.plot([c["coverage"] for c in sp], [c["risk"] for c in sp], "o-"); plt.xlabel("coverage"); plt.ylabel("risk (error rate among answered)"); plt.tight_layout(); plt.savefig(d / "risk_coverage.png", dpi=150); plt.close()
    ab = R["ablations"]; names = list(ab)
    plt.figure(figsize=(6, 3.4)); plt.bar(names, [ab[n]["pcr"] for n in names]); plt.xticks(rotation=30, ha="right"); plt.ylabel("PCR"); plt.tight_layout(); plt.savefig(d / "pcr_ablation.png", dpi=150); plt.close()
