# Architecture (as implemented)

This document describes what the code **actually does**. Where the proposal/review paper describes something not built, it is listed in `limitations.md`.

## Data flow

```
Form / API  ->  PatientState (versioned, hash-chained)  ->  PipelineRunner (LangGraph)
                                                           |
evaluate_delta --(red flags)--> emergency (short-circuit, no agents)
      |--(no significant delta, selective mode)--> carry_forward
      '--(first encounter / full re-run / significant delta)--> patient_data
patient_data -> ehr_analysis -> ( diagnostic || medical_knowledge || treatment ) -> join -> risk_assessment
   -> validate --(unsafe & reused reasoning, once)--> force_replan --> patient_data
   -> assemble (provenance gate, validation blocks, conflicts, abstention, optional LLM escalation for hard cases) -> explain -> Recommendation
```

Diagnostic, medical-knowledge and treatment run **in parallel** (LangGraph fan-out/fan-in), matching the review paper's specialised-agent layer; risk assessment, validation and explanation are sequential.

## Agents (all deterministic rule + retrieval cores; no LLM in the *decision* path - see "Optional LLM escalation" below for where an LLM can enter, and how it is fenced off from decisions)

| Agent | Group | Reads | Produces |
|---|---|---|---|
| patient_data | context | record | normalised, unit-canonical **facts** as claims; data-quality flags (never imputes) |
| ehr_analysis | context | version history | lab trajectories |
| diagnostic | reasoning | facts | ranked differential (never a single diagnosis); abstains without data |
| medical_knowledge | reasoning | facts | evidence pool via BM25 over the curated corpus; abstains if nothing retrieved |
| treatment | reasoning | facts | evidence-linked actions with pros/cons; one merged action per target |
| risk_assessment | reasoning | facts | risk flags (allergy, DDI, drug-disease, Beers, hepatic, pregnancy, data quality) |
| validation | safety | **ground-truth state** + all outputs | independent findings; blocks claims; flags fact-level corruption |
| explanation | safety | draft recommendation | clinician-readable text; optional guarded LLM rewording |

Downstream agents consume the *PatientDataAgent's claims* (not raw state), so an upstream error genuinely propagates unless validation stops it; that is what the fault-injection experiment measures.

## Temporal personalisation

`DeltaEngine` (rules in `config/significance_rules.yaml`) compares consecutive `PatientState` versions:
unit normalisation -> noise bands -> **asymmetric hysteresis** (an unsafe-direction threshold crossing is never suppressed; a return to the safe side inside the noise band is) -> significance in [0,1]. In selective mode the comparison is against the **anchor** (the state on which cached reasoning was last computed), not merely the previous encounter, so slow drift made of individually sub-noise steps is still detected (regression-tested). Deltas >= `SELECTIVE_REPLAN_THRESHOLD` select agents via `config/delta_agent_map.yaml`. The decision thresholds used by the agents (eGFR 60/45/30/20, K 5.5, HbA1c 7/8, SBP 130, ALT 3xULN, age 65) are mirrored in the delta rules; `tests/test_units_delta.py` and the boundary experiment guard that alignment. Unmodelled analytes fall back to a fail-safe default (0.35 -> re-plan), reported as `classifier="default"`.

Invocation groups: **reasoning** (4 agents), **context** (2 cheap builders, always refreshed on a re-plan and on carry-forward so facts are current), **safety** (validation + explanation, never skipped). "Zero reasoning agents on a noise-only encounter" is therefore the tested property; total agent calls on such an encounter are 4 (2 context + 2 safety) vs 8 for a full re-run.

## Optional LLM escalation for hard cases (`src/orchestrator/escalation.py`)

Added on explicit request: previously the only LLM touchpoint was the ExplanationAgent re-wording the
already-decided text. Now, inside `assemble` and only when an LLM is configured (`LLM_PROVIDER` != `none`),
a case is escalated to the model for a **secondary opinion** if - and only if - it is a "hard case":
the confidence that let the rule engine proceed sits just above the abstention line, or agents produced
conflicting flags/actions that had to be reconciled, or some claims were dropped as unsupported. A
comfortably-confident, conflict-free, fully-supported case is never escalated (cost, latency, and
unnecessary data exposure all argue against escalating everything).

The design constraint, tested in `tests/test_orchestrator.py`: the LLM can **only ever add**, never
change or remove, anything. It cannot touch `clinical_actions`, `risk_flags`, `aggregate_confidence`,
`calibrated_confidence` or the abstain/proceed decision - all of those are already finalised, from the
rule-based agents only, before escalation is even considered. Its output is split per line into atomic
claims and run through the `EvidenceLinker` in **strict** mode (every content word must occur in a retrieved
evidence sentence); only claims that restate retrieved evidence survive as `Recommendation.llm_escalation.
considerations` (the rest are counted, never surfaced). **This is a lexical check, not NLI**: in normal mode a probe let
through a false statement ("cross-reactivity ... is high") and a harmful fabricated one ("routinely receive penicillin
desensitisation"); strict mode blocks those (regression tests in `tests/test_llm_hardening.py`) but still admits some
scope swaps (precision 0.75 on the fresh labelled set, see `docs/results.md`). Considerations are therefore labelled
advisory/unverified in the console and the explanation, and in practice the step can only select and restate
evidence, not add new insight. The prompt is built only from
already-structured `Facts` (age band, conditions, meds, recent lab values, existing flags) - never raw
clinical notes - and still passes through the same PHI guard as the narration path. If the LLM is
unavailable or errors, the run completes normally (`status: "unavailable"`); this feature can never be
the reason a recommendation fails to come back.

HONEST STATUS: unit-tested with a scripted fake client only (including adversarial plausible-but-wrong fixtures added in the audit) - see
docs/limitations.md item 1 for the same caveat that already applied to narration: no live model was run
during this build (sandboxed environment, no internet to any provider's inference host).

## Safety and governance mechanisms (each has tests)

Clinician sign-off with named reviewer + written rationale (API) | provenance gate: claims/actions/flags need resolvable evidence, otherwise routed to a visible "withheld" panel | validation: fact fidelity, structured claim checks, evidence entailment (lexical), omission of safety flags, hard-contraindication conflicts | cycle-guarded safety re-plan | calibrated-abstention hook (defer when aggregate confidence < threshold) | emergency red-flag short-circuit | hash-chained audit log | PHI regex guard | invocation budget and timeout.

## Abstention (two scores)
Each run computes the fixed blend (`aggregate`, weights 0.4/0.3/0.2/0.1) and, if `config/abstention_weights.yaml` has `mode: logistic`, a fitted P(correct) over six features (agent, coverage, consistency, recency, data completeness, knowledge coverage). The policy defers when `min(calibrated, aggregate)` is below `ABSTENTION_THRESHOLD`. Refit with `python run.py calibrate --write`; the report is out-of-sample (disjoint seeds).

## LLM wiring
`ExplanationAgent` narration and escalation both take their client from `PipelineConfig.llm`. The API builds it once from `LLM_PROVIDER` via `get_llm_client()` (default `none`); `/health` reports `{provider, connected, error, local_only, escalation_enabled}` and the console header shows it. A misconfiguration (for example a cloud provider while `LLM_LOCAL_ONLY=true`) is reported, and the app keeps serving rule-based results. Narration is **append-only**: the deterministic explanation (with its disclaimer and findings) is never replaced, and the reworded text is added below it labelled unverified.
