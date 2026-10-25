import pytest

from src.agents import (DiagnosticAgent, EHRAnalysisAgent, MedicalKnowledgeAgent, PatientDataAgent, RiskAssessmentAgent, TreatmentAgent, ValidationAgent, ExplanationAgent)
from src.agents.common import MissingUpstreamError
from src.contracts.agent_io import AgentInput
from src.ingest.builders import build_state

CHAIN = [PatientDataAgent, EHRAnalysisAgent, DiagnosticAgent, MedicalKnowledgeAgent, TreatmentAgent, RiskAssessmentAgent]


def run_chain(state, prev=None):
    outs = {}
    for A in CHAIN:
        a = A()
        outs[a.name] = a.execute(AgentInput(agent_name=a.name, patient_state=state, previous_state=prev, upstream_outputs=dict(outs)))
    return outs


RICH = dict(conditions=["Type 2 diabetes mellitus (E11.9)"], meds=["Metformin 1000 mg BID", "Lisinopril 20 mg QD", "Warfarin 5 mg QD", "Ibuprofen 400 mg TID", "Amoxicillin 500 mg TID"],
            allergies=["Penicillin"], labs={"eGFR": 24, "Potassium": 5.8, "HbA1c": 9.4, "INR": 2.4}, vitals={"SBP": 148})


def test_all_eight_agents_exist_and_are_not_stubs():
    for A in CHAIN + [ValidationAgent, ExplanationAgent]:
        a = A()
        assert a.report_status()["healthy"] and "reads" in a.plan(None)


def test_agents_are_deterministic():
    s = build_state("P", "1955-03-01", "male", **RICH)
    a, b = run_chain(s), run_chain(s)
    for k in a:
        assert [c.claim_id for c in a[k].claims] == [c.claim_id for c in b[k].claims]
        assert [(x.action_type, x.target) for x in a[k].actions] == [(x.action_type, x.target) for x in b[k].actions]


def test_downstream_agents_require_patient_data():
    with pytest.raises(MissingUpstreamError):
        TreatmentAgent().execute(AgentInput(agent_name="treatment", patient_state=build_state("P")))


def test_patient_data_flags_missing_data_and_does_not_impute():
    s = build_state("P", "1955-03-01", "male", meds=["Metformin 500 mg BID"], labs={"HbA1c": 7.0})
    pd = PatientDataAgent().execute(AgentInput(agent_name="patient_data", patient_state=s))
    assert any("Missing eGFR" in f for f in pd.data_quality_flags) and not any(c.structured and c.structured.data.get("analyte") == "egfr" for c in pd.claims)
    assert pd.confidence < 0.9


def test_treatment_and_risk_on_rich_patient():
    o = run_chain(build_state("P", "1955-03-01", "male", **RICH))
    acts = {(a.action_type, a.target) for a in o["treatment"].actions}
    assert ("discontinue", "metformin") in acts and ("discontinue", "ibuprofen") in acts and ("discontinue", "amoxicillin") in acts and ("avoid", "penicillin-class antibiotics") in acts
    ids = {f.flag_id for f in o["risk_assessment"].risk_flags}
    assert {"RF-METFORMIN-EGFR30", "RF-DDI-IBUPROFEN-WARFARIN", "RF-HYPERK-RASI", "RF-ALLERGY-PENICILLIN", "RF-ALLERGY-CONFLICT-PENICILLIN-AMOXICILLIN"} <= ids
    assert all(a.claim_ids for a in o["treatment"].actions)                       # Rule 4 applies to actions
    assert all(f.evidence for f in o["risk_assessment"].risk_flags)


def test_no_drug_recommended_that_conflicts_with_allergy():
    o = run_chain(build_state("P", "1955-03-01", "male", meds=["Metformin 500 mg BID"], allergies=["Penicillin"], labs={"eGFR": 80}))
    assert not any(a.action_type in ("prescribe", "continue") and "penicillin" in a.target for a in o["treatment"].actions)


def test_diagnostic_gives_ranked_differential_and_detects_aki():
    prev = build_state("P", "1955-03-01", "male", labs={"eGFR": 80}, version=1)
    cur = build_state("P", "1955-03-01", "male", labs={"eGFR": 40}, version=2)
    d = run_chain(cur, prev)["diagnostic"]
    assert len(d.differential) >= 2 and [i.rank for i in d.differential] == sorted(i.rank for i in d.differential)
    assert "kidney injury" in d.differential[0].condition.lower()


def test_diagnostic_abstains_without_data():
    o = run_chain(build_state("P"))
    assert o["diagnostic"].abstained and o["diagnostic"].abstention_reason


def test_knowledge_agent_evidence_is_resolvable():
    from src.provenance import ProvenanceGate
    mk = run_chain(build_state("P", "1955-03-01", "male", **RICH))["medical_knowledge"]
    assert mk.claims and all(ProvenanceGate().is_grounded(c) for c in mk.claims)


def test_ehr_trajectory_uses_history():
    prev = build_state("P", labs={"eGFR": 80}, version=1); cur = build_state("P", labs={"eGFR": 50}, version=2)
    e = run_chain(cur, prev)["ehr_analysis"]
    assert e.claims[0].structured.data["values"] == [80.0, 50.0] and e.claims[0].structured.data["direction"] == "falling"


def test_validation_passes_clean_chain_and_catches_tampering():
    s = build_state("P", "1955-03-01", "male", **RICH)
    o = run_chain(s)
    v = ValidationAgent().execute(AgentInput(agent_name="validation", patient_state=s, upstream_outputs=o)).validation_report
    assert v.passed and v.checks_run > 20, v.findings
    o["patient_data"].claims = [c for c in o["patient_data"].claims if not (c.structured and c.structured.kind == "allergy_fact")]
    v2 = ValidationAgent().execute(AgentInput(agent_name="validation", patient_state=s, upstream_outputs=o)).validation_report
    assert not v2.passed and v2.unsafe_upstream and any(f.check == "fact_omitted" for f in v2.findings)


def test_resolved_condition_triggers_therapy_review():
    s = build_state("P", "1958-01-01", "male", conditions=[("I10", "Hypertension", "resolved")], meds=["Lisinopril 10 mg QD"], labs={"eGFR": 80})
    assert ("review", "lisinopril") in {(a.action_type, a.target) for a in run_chain(s)["treatment"].actions}


def test_unknown_drug_is_flagged_not_silently_ignored():
    """Drugs outside the knowledge table used to be silently un-assessed; now the limit is stated in the output."""
    s = build_state("P", "1955-03-01", "male", meds=["Acenocoumarol 2 mg QD", "Ibuprofen 400 mg TID"], labs={"eGFR": 80})
    o = run_chain(s)
    assert o["patient_data"].execution_metadata["unknown_meds"] == ["acenocoumarol"]
    f = [x for x in o["risk_assessment"].risk_flags if x.category == "data_quality" and "acenocoumarol" in x.involved]
    assert f and "NOT assessed" in f[0].description and f[0].claim_id
    assert o["patient_data"].confidence < 1.0


def test_possible_cross_reactivity_produces_a_review_action_regression():
    """Found by the held-out suite: a cephalosporin in a penicillin-allergic patient got a flag but no action."""
    o = run_chain(build_state("P", "1960-01-01", "female", meds=["Cefuroxime 500 mg BID"], allergies=["Penicillin (anaphylaxis)"], labs={"eGFR": 80}))
    a = [x for x in o["treatment"].actions if x.target == "cefuroxime"]
    assert a and a[0].action_type == "review" and a[0].claim_ids


def test_phase1_medication_safety_and_duplicate_therapies():
    """Tests new duplicate therapy detection and statin+fibrate interaction flags."""
    s = build_state("P", "1965-05-10", "male", meds=["Lisinopril 20 mg", "Enalapril 10 mg", "Atorvastatin 40 mg", "Gemfibrozil 600 mg"], labs={"eGFR": 75})
    o = run_chain(s)
    dup_flags = [f for f in o["risk_assessment"].risk_flags if f.category == "duplicate_therapy"]
    assert len(dup_flags) >= 1 and "ace_inhibitor" in dup_flags[0].description
    statin_flags = [f for f in o["risk_assessment"].risk_flags if "atorvastatin" in f.involved and "gemfibrozil" in f.involved]
    assert len(statin_flags) >= 1 and "myopathy" in statin_flags[0].description.lower()
