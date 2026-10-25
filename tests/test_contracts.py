import pytest
from datetime import datetime, timezone
from pydantic import ValidationError

from src.contracts import (AgentOutput, ClinicalClaim, ClaimStatus, EvidenceRef, Recommendation, RecommendationStatus, SourceType, DeltaReport, Delta, DeltaCategory)
from src.contracts.patient import Allergy, PatientState
from src.ingest.builders import build_state
from src.provenance import ProvenanceGate


def ev(i="EV-FDA-METFORMIN#0"):
    from src.agents.common import chunk
    return chunk(i)


def test_hash_is_deterministic_and_covers_all_clinical_content():
    a = build_state("P", labs={"eGFR": 50}, allergies=[], meds=["Metformin 500 mg BID"])
    assert a.content_hash() == build_state("P", labs={"eGFR": 50}, allergies=[], meds=["Metformin 500 mg BID"]).content_hash()
    # the scaffold ignored labs, allergies and demographics: each must now change the hash
    assert a.content_hash() != build_state("P", labs={"eGFR": 51}, meds=["Metformin 500 mg BID"]).content_hash()
    assert a.content_hash() != build_state("P", labs={"eGFR": 50}, allergies=["Penicillin"], meds=["Metformin 500 mg BID"]).content_hash()
    assert a.content_hash() != build_state("P", gender="female", labs={"eGFR": 50}, meds=["Metformin 500 mg BID"]).content_hash()


def test_allergy_has_no_implicit_clock():
    assert Allergy(substance="x").recorded_date is None


def test_patient_state_is_frozen():
    s = build_state("P")
    with pytest.raises(ValidationError):
        s.version = 5


def test_claim_without_evidence_cannot_be_supported():
    with pytest.raises(ValueError):
        ClinicalClaim(claim_id="c", assertion="x is true", confidence=0.9, status=ClaimStatus.SUPPORTED, agent_source="t")
    c = ClinicalClaim(claim_id="c", assertion="x is true", confidence=0.9, agent_source="t")   # pending without evidence is allowed, never approved
    assert c.status != ClaimStatus.SUPPORTED


def test_recommendation_governance():
    with pytest.raises(ValueError):
        Recommendation(recommendation_id="r", patient_id="p", explanation="hello world", status=RecommendationStatus.CLINICIAN_APPROVED, requires_clinician_review=True)
    with pytest.raises(ValueError):
        Recommendation(recommendation_id="r", patient_id="p", explanation="hello world", status=RecommendationStatus.CLINICIAN_APPROVED, requires_clinician_review=False)
    r = Recommendation(recommendation_id="r", patient_id="p", explanation="hello world", status=RecommendationStatus.CLINICIAN_APPROVED, requires_clinician_review=False, reviewed_by="DR-X")
    assert r.reviewed_by == "DR-X"


def test_abstaining_agent_needs_reason():
    with pytest.raises(ValueError):
        AgentOutput(agent_name="a", abstained=True)


def test_delta_report_threshold_is_configurable():
    d = Delta(category=DeltaCategory.LAB_TREND_SHIFT, field_path="x", significance=0.4, rationale="rule text")
    assert DeltaReport(patient_id="p", current_version=2, deltas=[d], threshold=0.3).is_clinically_significant
    assert not DeltaReport(patient_id="p", current_version=2, deltas=[d], threshold=0.5).is_clinically_significant


def test_provenance_gate_does_not_mutate_and_checks_resolvability():
    good = ClinicalClaim(claim_id="g", assertion="abc claim", confidence=.9, evidence=[ev()], agent_source="t")
    fake = ClinicalClaim(claim_id="f", assertion="abc claim", confidence=.9, evidence=[ev().model_copy(update={"id": "EV-FAKE#0"})], agent_source="t")
    none = ClinicalClaim(claim_id="n", assertion="abc claim", confidence=.9, agent_source="t")
    ok, bad = ProvenanceGate().partition([good, fake, none])
    assert [c.claim_id for c in ok] == ["g"] and {c.claim_id for c in bad} == {"f", "n"}
    assert fake.status == ClaimStatus.PENDING          # input untouched
    assert ProvenanceGate.compute_unsupported_claim_rate([good, fake, none]) == pytest.approx(2 / 3, abs=1e-3)


def test_tampered_snippet_is_unresolvable():
    e = ev().model_copy(update={"snippet": "Metformin is perfectly safe at any eGFR."})
    assert not ProvenanceGate().is_grounded(ClinicalClaim(claim_id="t", assertion="abc claim", confidence=.9, evidence=[e], agent_source="t"))
