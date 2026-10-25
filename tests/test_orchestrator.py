import pytest

from src.contracts.agent_io import ExecutionMode
from src.ingest.builders import build_state
from src.orchestrator import InvocationBudgetExceeded, PipelineConfig, PipelineRunner, PipelineTimeout, build_orchestrator_graph
from eval.metrics import recommendation_signature as sig

BASE = dict(conditions=["Type 2 diabetes mellitus (E11.9)", "Hypertension (I10)"], meds=["Metformin 1000 mg BID", "Lisinopril 20 mg QD"])


def enc(v, when, **kw):
    d = {**BASE, "labs": {"eGFR": 72, "Potassium": 4.4, "HbA1c": 6.9}, "vitals": {"SBP": 124}}
    d.update(kw)
    return build_state("P", "1958-05-10", "male", timestamp=when, version=v, **d)


def test_graph_compiles_and_contains_real_nodes():
    g = build_orchestrator_graph().get_graph()
    assert {"evaluate_delta", "carry_forward", "patient_data", "treatment", "risk_assessment", "validate", "assemble", "explain", "force_replan"} <= set(g.nodes)


def test_first_encounter_runs_everything(runner):
    m = runner.run(enc(1, "2026-01-10")).recommendation.metrics
    assert (m.reasoning_invocations, m.context_invocations, m.safety_invocations) == (4, 2, 2) and m.agent_invocations == 8


def test_noise_only_encounter_invokes_zero_reasoning_agents_but_still_validates_and_explains(runner):
    r1 = runner.run(enc(1, "2026-01-10"))
    r2 = runner.run(enc(2, "2026-04-10", labs={"eGFR": 71, "Potassium": 4.5, "HbA1c": 6.8}, vitals={"SBP": 126}), prev=enc(1, "2026-01-10"), prev_run=r1)
    m = r2.recommendation.metrics
    assert m.reasoning_invocations == 0 and m.safety_invocations == 2 and m.safety_replans == 0
    assert [t.status for t in r2.recommendation.trace if t.agent in ("diagnostic", "treatment")] == ["skipped_carry_forward"] * 2
    assert sig(r1.recommendation) == sig(r2.recommendation)
    assert r2.recommendation.validation_report is not None and "No clinically significant change" in " ".join(r2.recommendation.what_changed)


def test_new_allergy_reruns_only_affected_agents(runner):
    p, c = enc(1, "2026-01-10"), enc(2, "2026-04-10", allergies=["Penicillin (rash)"])
    r1 = runner.run(p)
    r2 = runner.run(c, prev=p, prev_run=r1)
    ran = {t.agent for t in r2.recommendation.trace if t.status == "ran"}
    reused = {t.agent for t in r2.recommendation.trace if t.status == "reused_cached"}
    assert {"treatment", "risk_assessment", "validation"} <= ran and {"diagnostic", "medical_knowledge"} <= reused


def test_both_modes_produce_schema_identical_recommendations(runner):
    p, c = enc(1, "2026-01-10"), enc(2, "2026-07-10", labs={"eGFR": 24, "Potassium": 5.8, "HbA1c": 9.4})
    a = runner.run(c, prev=p, mode=ExecutionMode.FULL_RERUN).recommendation
    b = runner.run(c, prev=p, prev_run=runner.run(p), mode=ExecutionMode.SELECTIVE_REPLAN).recommendation
    assert set(a.model_dump()) == set(b.model_dump()) and sig(a) == sig(b)
    assert a.metrics.agent_invocations == 8


def test_recommendation_is_never_final_and_carries_explanation(runner):
    r = runner.run(enc(1, "2026-01-10")).recommendation
    assert r.requires_clinician_review and r.status.value in ("requires_clinician_review", "escalated") and "DECISION SUPPORT ONLY" in r.explanation


def test_safety_replan_triggers_when_reused_reasoning_contradicts_record(runner):
    """Sabotage the delta engine so a real change is treated as noise: validation must catch the stale reasoning."""
    class Blind(type(runner.delta_engine)):
        def compute(self, prev, curr):
            rep = super().compute(prev, curr)
            rep.deltas = [d for d in rep.deltas if False]
            rep.is_clinically_significant = False
            return rep
    r = PipelineRunner()
    r.delta_engine = Blind()
    p = enc(1, "2026-01-10", allergies=[], meds=["Metformin 1000 mg BID"])
    c = enc(2, "2026-04-10", meds=["Metformin 1000 mg BID"], allergies=["Penicillin"], labs={"eGFR": 24, "Potassium": 4.4, "HbA1c": 6.9})
    res = r.run(c, prev=p, prev_run=r.run(p))
    assert res.recommendation.metrics.safety_replans == 1 and any(a.target == "metformin" and a.action_type == "discontinue" for a in res.recommendation.clinical_actions)
    off = PipelineRunner(PipelineConfig(enable_safety_replan=False)); off.delta_engine = Blind()
    res2 = off.run(c, prev=p, prev_run=off.run(p))
    assert res2.recommendation.deferred                     # without the re-plan it must at least withhold, never approve stale advice


def test_cycle_guard_limits_replans():
    r = PipelineRunner()
    assert r.route_after_validate({"validation": type("V", (), {"unsafe_upstream": True})(), "route": "full", "cycles": 0}) == "assemble"
    assert r.route_after_validate({"validation": type("V", (), {"unsafe_upstream": True})(), "route": "carry", "cycles": 1}) == "assemble"


def test_invocation_budget_and_timeout_are_enforced():
    with pytest.raises(InvocationBudgetExceeded):
        PipelineRunner(PipelineConfig(max_invocations=3)).run(enc(1, "2026-01-10"))
    with pytest.raises(PipelineTimeout):
        PipelineRunner(PipelineConfig(timeout_s=1e-9)).run(enc(1, "2026-01-10"))


def test_emergency_red_flag_short_circuits(runner):
    s = enc(1, "2026-01-10", notes=["crushing chest pain radiating to the arm"])
    r = runner.run(s).recommendation
    assert r.deferred and r.emergency_flags and r.metrics.agent_invocations == 0 and "EMERGENCY" in r.explanation


def test_missing_critical_data_lowers_confidence(runner):
    full = runner.run(enc(1, "2026-01-10")).recommendation
    miss = runner.run(build_state("P", "1958-05-10", "male", **BASE)).recommendation
    assert miss.aggregate_confidence < full.aggregate_confidence
    assert any("Missing" in f.description for f in miss.risk_flags)


def test_conflict_resolution_surfaces_both_positions():
    from src.contracts.recommendation import ClinicalAction, RiskFlag, RiskSeverity
    from src.orchestrator import resolve_conflicts
    a = ClinicalAction(action_type="continue", target="Metformin", details="keep going", rationale="x", source_agent="treatment")
    f = RiskFlag(flag_id="RF-X", severity=RiskSeverity.CRITICAL, category="contraindication", description="Metformin contraindicated", involved=["metformin"], source_agent="risk_assessment")
    c = resolve_conflicts([a], [f])
    assert len(c) == 1 and "continue" in c[0].position_a and "contraindicated" in c[0].position_b


def test_slow_drift_of_sub_noise_steps_eventually_replans_regression(runner):
    """Comparing only consecutive encounters let HbA1c 7.6 -> 7.4 -> 7.2 -> 7.0 -> 6.8 ratchet unnoticed. The anchor fixes it."""
    seq = [7.6, 7.4, 7.2, 7.0, 6.8]
    states = [enc(i + 1, f"2026-0{i + 1}-10", labs={"eGFR": 72, "Potassium": 4.4, "HbA1c": v}) for i, v in enumerate(seq)]
    prev_run, prev, routes = None, None, []
    for s in states:
        r = runner.run(s, prev=prev, prev_run=prev_run)
        routes.append(r.recommendation.metrics.reasoning_invocations)
        prev, prev_run = s, r
    assert routes[1:3] == [0, 0]            # first small steps are noise
    assert any(x > 0 for x in routes[3:])   # cumulative drift beyond the noise band is detected against the anchor


def test_fresh_action_at_egfr_31_is_not_rejected_by_validation_regression(runner):
    r = runner.run(enc(1, "2026-01-10", labs={"eGFR": 32, "Potassium": 4.4, "HbA1c": 6.9})).recommendation
    assert not r.deferred and r.validation_report.passed and any(a.action_type == "adjust_dose" and a.target == "metformin" for a in r.clinical_actions)


# ------------------------------------------------------------------ LLM escalation (Part C.6 extension)
def test_llm_escalation_not_triggered_without_llm_or_on_comfortable_case(runner):
    """No LLM configured at all -> escalation cleanly reports unavailable/not_triggered, run still succeeds
    (this optional feature must never be the reason a clinical recommendation fails to come back)."""
    r = runner.run(enc(1, "2026-01-10")).recommendation
    assert r.llm_escalation is not None and r.llm_escalation["triggered"] is False


def test_llm_escalation_triggers_and_only_adds_evidence_linked_considerations():
    """Forced into the 'hard case' band (margin=1.0, threshold=0.0 -> every non-deferred case qualifies).
    One scripted response is verbatim evidence-corpus text (must pass the linker); a second response
    (queued for the unrelated explanation-narration LLM call) contains fabricated numbers (must be
    rejected by that separate guard). Crucially: clinical_actions/risk_flags must be untouched by either -
    the LLM can only ever add advisory 'considerations', never a decision."""
    from src.llm import ScriptedLLMClient
    llm = ScriptedLLMClient(["Metformin should be discontinued if eGFR later falls below 30 mL/min/1.73 m2.",
                            "The patient has exactly 17 fingers and takes 999mg of magicillin."])
    rn = PipelineRunner(PipelineConfig(llm=llm, llm_escalation_margin=1.0, abstention_threshold=0.0))
    r = rn.run(enc(1, "2026-01-10", labs={"eGFR": 24, "Potassium": 4.4, "HbA1c": 6.9})).recommendation
    esc = r.llm_escalation
    assert esc["triggered"] and esc["status"] == "ok"
    assert esc["considerations"] == ["Metformin should be discontinued if eGFR later falls below 30 mL/min/1.73 m2"]
    assert esc["n_dropped_unsupported"] == 0 and esc["n_proposed"] == 1
    assert any(a.action_type == "discontinue" and a.target == "metformin" for a in r.clinical_actions)   # unchanged, rule-based
    assert "narration rejected" in r.explanation and "999" in r.explanation and "17" in r.explanation      # guard caught it and SAID SO (not silently swapped in)
    assert "17 fingers" not in r.explanation and "magicillin" not in r.explanation                          # but the fabricated sentence itself never appears verbatim


def test_llm_escalation_drops_fabricated_or_unlinkable_statements():
    from src.llm import ScriptedLLMClient
    llm = ScriptedLLMClient(["The patient owns exactly 17 unicorns and takes 999mg of magicillin daily."])
    rn = PipelineRunner(PipelineConfig(llm=llm, llm_escalation_margin=1.0, abstention_threshold=0.0))
    r = rn.run(enc(1, "2026-01-10", labs={"eGFR": 24, "Potassium": 4.4, "HbA1c": 6.9})).recommendation
    assert r.llm_escalation["status"] == "ok" and r.llm_escalation["considerations"] == [] and r.llm_escalation["n_dropped_unsupported"] >= 1


def test_llm_escalation_failure_degrades_gracefully_never_blocks_the_run():
    """An LLM that raises on every call must not prevent a recommendation from being produced."""
    from src.llm.client import LLMClient, LLMError

    class Boom(LLMClient):
        name = "boom"

        def generate(self, prompt, system=None):
            raise LLMError("simulated provider outage")

    rn = PipelineRunner(PipelineConfig(llm=Boom(), llm_escalation_margin=1.0, abstention_threshold=0.0))
    r = rn.run(enc(1, "2026-01-10", labs={"eGFR": 24, "Potassium": 4.4, "HbA1c": 6.9})).recommendation
    assert r.llm_escalation["triggered"] and r.llm_escalation["status"] == "unavailable"
    assert not r.deferred and any(a.action_type == "discontinue" and a.target == "metformin" for a in r.clinical_actions)


def test_llm_escalation_triggers_on_conflicts_and_on_unsupported_claims():
    from src.orchestrator.escalation import check_trigger
    assert check_trigger(0.95, 0.15, 0.6, n_conflicts=1, n_unsupported=0).should_escalate
    assert check_trigger(0.95, 0.15, 0.6, n_conflicts=0, n_unsupported=1).should_escalate
    assert not check_trigger(0.95, 0.15, 0.6, n_conflicts=0, n_unsupported=0).should_escalate
    assert check_trigger(0.61, 0.15, 0.6, n_conflicts=0, n_unsupported=0).should_escalate    # ambiguous band just above abstention threshold
    assert not check_trigger(0.90, 0.15, 0.6, n_conflicts=0, n_unsupported=0).should_escalate  # comfortably above the band
