# Evaluation results (auto-generated)

Generated 2026-10-03T19:35:51.663724+00:00 | seeds [0] | quick=True | runtime 48.0s

> Every number below is computed from real pipeline runs by `python run.py eval`. Data are synthetic; agents are deterministic rule+retrieval cores (no LLM).
> Do not quote these as clinical performance. They evaluate the software design on author-built scenarios.

## Findings (text derived from the numbers)

- Adaptation recall (10 authored change families): full re-run 1.000 +/- 0.000 (n=1), selective 1.000 +/- 0.000 (n=1). Both modes satisfy every ground-truth expectation, so this suite does not separate them (it is a correctness check of the rule set).
- Held-out adaptation suite (13 scenarios written from clinical guidance before being run; author-written, so only semi-independent): full re-run 1.000 +/- 0.000 (n=1), selective 1.000 +/- 0.000 (n=1). On its first run 12/13 passed; the failure (cephalosporin in a penicillin-allergic patient produced a flag but no action) was a real clinical gap, fixed without editing the expectation.
- Evidence linker (lexical, not NLI) on labelled claim/evidence pairs: dev n=24 precision 1.000 recall 0.833; test n=24 precision 1.000 recall 1.000; test2 n=16 precision 0.667 recall 0.889. `dev` guided the rules; `test` is CONTAMINATED (its baseline errors were seen while designing rules); `test2` was written afterwards and is the honest estimate - it still admits false positives (antonym adjectives, 'type 1' vs 'type 2', unknown words, re-used numbers). Baseline (before the rewrite) was ~0.50-0.61 precision.
- Abstention calibration, out-of-sample (414 test cases, seeds disjoint from training): fixed-weight blend AURC 0.0717, AUROC 0.849, mean confidence correct/incorrect 0.939/0.910; learned logistic AURC 0.0659, AUROC 0.876, mean confidence 0.858/0.471. Learned weights: {'agent': 0.79, 'coverage': 0.0, 'consistency': 0.0, 'recency': 0.224, 'completeness': -0.022, 'knowledge': 0.735}. Accuracy by case kind: {'normal': {'n': 69, 'correct': 1.0}, 'missing_labs': {'n': 69, 'correct': 0.652}, 'unknown_drug': {'n': 69, 'correct': 0.217}, 'fault': {'n': 207, 'correct': 0.913}}. The pool contains error sources (missing labs, drugs outside the table) that the features were designed to detect; injected faults with validation off are NOT separable by these features.
- RSI on semantics-preserving perturbations: full re-run 1.000, selective 1.000. Identical because the agents are deterministic: the stochastic LLM-drift problem that motivates RSI cannot manifest without LLM-backed agents (not built).
- Threshold flicker (value moves back across a decision threshold within assay noise): stability full re-run 0.00 vs selective 1.00; responsiveness to unsafe-direction crossings full re-run 1.00 vs selective 1.00. This is the only place the temporal design measurably changes stability, and it is a structural effect (hysteresis), not LLM noise. These 10 cases were chosen by the author to straddle thresholds.
- Efficiency depends entirely on how often true change occurs (a modelling assumption of the synthetic simulator): signal fraction 0.00 -> total invocations -49%, reasoning invocations -98%; signal fraction 0.12 -> total invocations -43%, reasoning invocations -86%; signal fraction 0.20 -> total invocations -41%, reasoning invocations -82%; signal fraction 0.30 -> total invocations -37%, reasoning invocations -74%.
- DeltaEngine vs ground-truth events (p=0.2): signal recall 1.000, false-positive rate 0.000; selective/full recommendations agree on 100.0% of encounters; safety re-plans triggered: 0.
- Propagation containment over 11 injected faults: PCR 1.000 with defences vs 0.000 with validation, provenance gate and abstention disabled. Faults are author-designed and detectable by the checks the author wrote; PCR = 1.0 shows the checks work on these fault classes, not that unseen failure modes are contained.
- Ablations that changed at least one metric: no_validation, no_defences.
- Selective prediction (AURC, lower is better) over 50 degraded/faulted cases: 0.317; overall error 0.300; mean confidence correct 0.941 vs incorrect 0.935.

## Evidence linker (precision / recall on labelled pairs)

| split | n | precision | recall | FP rate | note |
|---|---|---|---|---|---|
| dev | 24 | 1.000 | 0.833 | 0.000 | guided the rules |
| test | 24 | 1.000 | 1.000 | 0.000 | contaminated (errors seen) |
| test2 | 16 | 0.667 | 0.889 | 0.571 | written after; honest estimate |

Strict mode (used for LLM-originated text: every content word must occur in the evidence):

| split | n | precision | recall | FP rate |
|---|---|---|---|---|
| dev | 24 | 1.000 | 0.250 | 0.000 |
| test | 24 | 1.000 | 0.250 | 0.000 |
| test2 | 16 | 0.750 | 0.667 | 0.286 |

Remaining errors on test2 (non-strict):

- FN: Managing hyperkalemia from RAS inhibitors usually starts with measures that lower potassium
- FP: Cross-reactivity between penicillins and cephalosporins is high overall
- FP: SGLT2 inhibitors are recommended for patients with type 1 diabetes, CKD and an eGFR of 20 mL/min/1.73 m2 or higher
- FP: Combining two or more QT-prolonging drugs raises the risk of torsades de pointes and should be encouraged
- FP: G2 is 60 to 90 mL/min/1.73 m2

## Ablations

| config | AR (selective) | PCR | detect | UCR approved | agree sel/full | safety replans | invocation reduction |
|---|---|---|---|---|---|---|---|
| full_system | 1.000 | 1.000 | 1.000 | 0.000 | 1.000 | 0 | 0.425 |
| no_provenance_gate | 1.000 | 1.000 | 1.000 | 0.000 | 1.000 | 0 | 0.425 |
| no_validation | 1.000 | 0.273 | 0.273 | 0.000 | 1.000 | 0 | 0.486 |
| no_abstention | 1.000 | 1.000 | 1.000 | 0.000 | 1.000 | 0 | 0.425 |
| no_safety_replan | 1.000 | 1.000 | 1.000 | 0.000 | 1.000 | 0 | 0.425 |
| no_defences | 1.000 | 0.000 | 0.000 | 0.004 | 1.000 | 0 | 0.486 |

## PCR by fault type and injection point (full system)

| fault | point | n | PCR |
|---|---|---|---|
| fabricated_interaction | risk_assessment | 1 | 1.00 |
| fabricated_interaction | treatment | 1 | 1.00 |
| inverted_lab_value | patient_data | 1 | 1.00 |
| wrong_unit | patient_data | 1 | 1.00 |
| hallucinated_citation_id | medical_knowledge | 1 | 1.00 |
| hallucinated_citation_id | treatment | 1 | 1.00 |
| hallucinated_citation_id | risk_assessment | 1 | 1.00 |
| plausible_wrong_contraindication | risk_assessment | 1 | 1.00 |
| plausible_wrong_contraindication | treatment | 1 | 1.00 |
| dropped_allergy | patient_data | 1 | 1.00 |
| dropped_allergy | risk_assessment | 1 | 1.00 |

## RSI by perturbation

| perturbation | full re-run | selective |
|---|---|---|
| repeat_run | 1.00 | 1.00 |
| reorder_fields | 1.00 | 1.00 |
| equivalent_units | 1.00 | 1.00 |
| irrelevant_demographics | 1.00 | 1.00 |
| notes_paraphrase | 1.00 | 1.00 |
| assay_jitter | 1.00 | 1.00 |

## Failures

- none in the adaptation suite
