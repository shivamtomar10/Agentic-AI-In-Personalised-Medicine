import pytest

from eval.faults import FAULT_MATRIX
from eval.harness import experiments as X
from eval.metrics import (FaultOutcome, aurc, breakdown, check_expectations, compute_adaptation_recall, compute_pcr, compute_rsi, compute_ucr, reduction, risk_coverage_curve)
from src.contracts.recommendation import ClinicalAction, Recommendation


def rec(actions=(), deferred=False):
    return Recommendation(recommendation_id="r", patient_id="p", explanation="hello world", deferred=deferred,
                          clinical_actions=[ClinicalAction(action_type=t, target=g, details="d", rationale="r") for t, g in actions])


def test_pcr_denominator_counts_all_injected_faults():
    """Regression: the scaffold divided by DETECTED faults only and returned 1.0 when nothing was detected."""
    outs = [FaultOutcome("k", "p", detected=False, contained=False)] * 4
    assert compute_pcr(outs) == 0.0
    assert compute_pcr(outs + [FaultOutcome("k", "p", True, True)]) == pytest.approx(0.2)
    assert compute_pcr([]) is None
    assert breakdown(outs)["k"]["p"] == {"n": 4, "pcr": 0.0}


def test_rsi_is_semantic_not_textual():
    a = rec([("discontinue", "metformin")]); b = rec([("discontinue", "metformin")]); b.clinical_actions[0].rationale = "completely different words"
    assert compute_rsi([(a, b)]) == 1.0 and compute_rsi([(a, rec([("adjust_dose", "metformin")]))]) == 0.0 and compute_rsi([]) is None


def test_check_expectations():
    ok, miss = check_expectations(rec([("discontinue", "metformin")]), {"required_actions": [["discontinue", "metformin"]], "forbidden_actions": [["continue", "metformin"]]})
    assert ok and not miss
    ok, miss = check_expectations(rec(deferred=True), {"required_flags": ["RF-X"]})
    assert not ok and len(miss) == 2
    assert compute_adaptation_recall([True, False]) == 0.5


def test_aurc_perfect_and_worst_ordering():
    assert aurc([0.9, 0.8, 0.2, 0.1], [True, True, False, False]) == pytest.approx(0.0 + 0.0 + (1 / 3 + 2 / 4) / 4)
    assert aurc([0.9, 0.8, 0.2, 0.1], [False, False, True, True]) > aurc([0.9, 0.8, 0.2, 0.1], [True, True, False, False])
    assert risk_coverage_curve([0.5, 0.9], [True, False])[0][1] == 1.0


def test_reduction():
    assert reduction([8, 8], [4, 4]) == 0.5


def test_fault_matrix_covers_six_fault_types():
    assert set(FAULT_MATRIX) == {"fabricated_interaction", "inverted_lab_value", "wrong_unit", "hallucinated_citation_id", "plausible_wrong_contraindication", "dropped_allergy"}


def test_faults_are_contained_with_defences_and_escape_without():
    on, off = X.faults([0]), X.faults([0], "no_defences")
    assert on["pcr"] == 1.0 and on["n_injected"] == 11
    assert off["pcr"] == 0.0                       # the harness is sensitive: with defences off every fault reaches the output
    assert X.faults([0], "no_validation")["pcr"] < 1.0


def test_metrics_are_computed_not_simulated__mutation_test(monkeypatch):
    """If the TreatmentAgent is sabotaged, adaptation recall MUST fall. (The scaffold's demo could never fail.)"""
    assert X.adaptation([0])["per_mode"]["selective_replan"]["mean"] == 1.0
    from src.agents.treatment import TreatmentAgent
    orig = TreatmentAgent._run
    def broken(self, inp):
        out = orig(self, inp)
        out.actions = [a for a in out.actions if a.target != "metformin"]
        return out
    monkeypatch.setattr(TreatmentAgent, "_run", broken)
    X._RUNNERS.clear()
    a = X.adaptation([0])
    X._RUNNERS.clear()
    assert a["per_mode"]["selective_replan"]["mean"] < 1.0 and a["failures"]


def test_hysteresis_is_measured_at_decision_boundaries():
    b = X.boundaries()
    assert b["to_safe_stability"]["selective"] > b["to_safe_stability"]["baseline"]
    assert b["to_unsafe_responsiveness"]["selective"] == 1.0            # never suppress a genuinely unsafe crossing


def test_cohort_efficiency_and_engine_quality():
    c = X.cohort([0], 6, 6, 0.2)
    assert 0 < c["invocation_reduction"] < 1 and c["reasoning_reduction"] > c["invocation_reduction"]
    assert c["delta_engine"]["recall_signal"] == 1.0 and c["delta_engine"]["false_positive_rate"] == 0.0
    assert c["selective_vs_full_agreement"] > 0.95


def test_ucr_raw_counts_unsupported():
    from src.contracts.evidence import ClinicalClaim
    assert compute_ucr([ClinicalClaim(claim_id="c", assertion="abc claim", confidence=.5, agent_source="t")]) == 1.0
