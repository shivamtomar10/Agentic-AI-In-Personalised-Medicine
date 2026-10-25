import json

import pytest
from fastapi.testclient import TestClient

from src.api import main
from tests.helpers import Actor

PAY = {"conditions": ["Type 2 diabetes (E11.9)"],
       "medications": ["Metformin 1000 mg BID", "Lisinopril 20 mg QD"], "allergies": ["Penicillin (rash)"],
       "labs": {"egfr": 24, "hba1c": 7.8, "potassium": 5.3}, "vitals": {"systolic_bp": 148}, "chief_complaint": "routine"}


@pytest.fixture()
def env(tmp_path):
    main.reset_context(str(tmp_path / "audit.jsonl"), invite_code="INVITE-1")


@pytest.fixture()
def pat(env):
    a = Actor()
    assert a.register_patient().status_code == 201
    return a


@pytest.fixture()
def doc(env):
    a = Actor()
    assert a.register_clinician().status_code == 201
    return a


def test_health_is_public_and_minimal(env):
    r = TestClient(main.app).get("/health").json()
    assert r["status"] == "ok" and "llm" not in r and "audit_chain_ok" not in r          # details are clinician-only


def test_form_payload_reaches_the_pipeline_regression(pat):
    """Scaffold bug: the labs typed in the form never reached the backend, so flags were always empty."""
    r = pat.post("/api/v1/patient/analyse", json=PAY).json()
    texts = " ".join(i["text"] for i in r["items_for_clinician"])
    assert "Metformin is contraindicated at eGFR 24" in texts and "penicillin" in texts.lower()
    assert "discontinue metformin" in r["proposed_actions"] and r["requires_clinician_review"] is True
    for k in ("recommendation_id", "state_version", "what_changed", "agent_invocations", "carried_forward", "aggregate_confidence", "n_evidence", "disclaimer", "items_for_clinician"):
        assert k in r                                                       # the exact keys the portal reads


def test_patient_identity_comes_from_the_session_not_the_body(pat, doc):
    """A patient can't submit records under someone else's ID: the body's patient_id is ignored."""
    r = pat.post("/api/v1/patient/analyse", json={**PAY, "patient_id": "PT-VICTIM"}).json()
    rec = doc.get(f"/api/v1/recommendations/{r['recommendation_id']}").json()
    assert rec["patient_id"] == pat.patient_id != "PT-VICTIM"
    assert "PT-VICTIM" not in [p["patient_id"] for p in doc.get("/api/v1/patients").json()["patients"]]


def test_full_recommendation_shape_used_by_console(pat, doc):
    rid = pat.post("/api/v1/patient/analyse", json=PAY).json()["recommendation_id"]
    r = doc.get(f"/api/v1/recommendations/{rid}").json()
    for k in ("trace", "metrics", "delta_report", "clinical_actions", "risk_flags", "supported_claims", "flagged_claims", "flagged_actions", "flagged_risk_flags", "validation_report", "explanation", "deferred", "emergency_flags"):
        assert k in r
    ev = r["supported_claims"][0]["evidence"][0]
    assert {"id", "title", "source", "snippet", "verbatim"} <= set(ev)
    assert {"agent", "status", "reason", "n_claims", "latency_ms"} <= set(r["trace"][0])


def test_longitudinal_second_submission_is_selective(pat):
    pat.post("/api/v1/patient/analyse", json=PAY)
    r = pat.post("/api/v1/patient/analyse", json={**PAY, "labs": {"egfr": 23.5, "hba1c": 7.8, "potassium": 5.3}}).json()
    assert r["state_version"] == 2 and r["carried_forward"] is True and r["reasoning_invocations"] == 0
    r_full = pat.post("/api/v1/patient/analyse?mode=full_rerun", json={**PAY, "labs": {"egfr": 23.4, "hba1c": 7.8, "potassium": 5.3}}).json()
    assert r_full["agent_invocations"] == 8


def test_review_workflow_and_governance(pat, doc):
    rid = pat.post("/api/v1/patient/analyse", json=PAY).json()["recommendation_id"]
    body = {"action": "approve", "rationale": "reviewed and agree"}
    assert doc.post("/api/v1/recommendations/NOPE/review", json=body).status_code == 404            # scaffold approved unknown ids
    assert doc.post(f"/api/v1/recommendations/{rid}/review", json={**body, "rationale": ""}).status_code == 422
    assert doc.post(f"/api/v1/recommendations/{rid}/review", json={**body, "action": "modify"}).status_code == 422
    ok = doc.post(f"/api/v1/recommendations/{rid}/review", json={**body, "clinician_id": "DR-SOMEONE-ELSE"}).json()
    assert ok["status"] == "clinician_approved" and ok["reviewed_by"] == "DR-TEST"                 # identity = signed-in account, not the body
    assert doc.post(f"/api/v1/recommendations/{rid}/review", json=body).status_code == 409
    full = doc.get(f"/api/v1/recommendations/{rid}").json()
    assert full["requires_clinician_review"] is False and full["review_notes"] == "reviewed and agree"
    ev = [e for e in main.ctx.audit.read_all() if e["event"] == "clinician_review"]
    assert len(ev) == 1 and ev[0]["payload"]["clinician_id"] == "DR-TEST" and main.ctx.audit.verify_chain()


def test_patient_history_is_scoped_to_the_signed_in_patient(pat, doc):
    rid1 = pat.post("/api/v1/patient/analyse", json=PAY).json()["recommendation_id"]
    other = Actor()
    other.register_patient(email="other@example.com")
    other.post("/api/v1/patient/analyse", json=PAY)
    hist = pat.get("/api/v1/patient/me/recommendations").json()
    assert hist["count"] == 1 and hist["items"][0]["recommendation_id"] == rid1 and hist["items"][0]["reviewed"] is False
    doc.post(f"/api/v1/recommendations/{rid1}/review", json={"action": "approve", "rationale": "Looks appropriate."})
    assert pat.get("/api/v1/patient/me/recommendations").json()["items"][0]["status"] == "clinician_approved"
    tl = pat.get("/api/v1/patient/me/timeline").json()["versions"]
    assert len(tl) == 1 and "state_hash" not in tl[0] and tl[0]["labs"]


def test_patient_sees_why_and_evidence_but_not_internal_process(pat, doc):
    """A flag or action must never reach the patient as a bare label: it carries its rationale and sources.
    Internal machinery (agent trace, agent-vs-agent conflicts, the content of anything withheld) stays clinician-only."""
    r = pat.post("/api/v1/patient/analyse", json=PAY).json()
    flag = next(i for i in r["items_for_clinician"] if "metformin" in i["text"].lower())
    assert flag["why"] and flag["evidence"] and {"title", "source", "snippet"} <= set(flag["evidence"][0])
    action = next(a for a in r["action_details"] if "metformin" in a["summary"].lower())
    assert action["rationale"] and action["evidence"] and action["urgency"] in ("urgent", "high", "moderate", "low")
    assert set(r["proposed_actions"]) == {a["summary"] for a in r["action_details"]}          # unchanged, backward-compatible field
    for hidden in ("trace", "conflicts", "flagged_claims", "flagged_actions", "flagged_risk_flags", "validation_report", "explanation"):
        assert hidden not in r


def test_patient_status_reflects_clinician_review_and_enforces_ownership(pat, doc):
    rid = pat.post("/api/v1/patient/analyse", json=PAY).json()["recommendation_id"]
    before = pat.get(f"/api/v1/patient/status/{rid}").json()
    assert before["reviewed"] is False and before["reviewed_by"] is None and before["requires_clinician_review"] is True
    doc.post(f"/api/v1/recommendations/{rid}/review", json={"action": "approve", "rationale": "reviewed and agree"})
    after = pat.get(f"/api/v1/patient/status/{rid}").json()
    assert after["reviewed"] is True and after["reviewed_by"] == "DR-TEST" and after["clinician_notes"] == "reviewed and agree"
    assert after["status"] == "clinician_approved" and after["requires_clinician_review"] is False
    assert pat.get("/api/v1/patient/status/NOPE").status_code == 404
    intruder = Actor()
    intruder.register_patient(email="intruder@example.com")
    assert intruder.get(f"/api/v1/patient/status/{rid}").status_code == 404                          # someone else's record looks like a missing one
    assert TestClient(main.app).get(f"/api/v1/patient/status/{rid}").status_code == 401             # and signed-out browsers get nothing


def test_emergency_and_deferred_review_rules(pat, doc):
    r = pat.post("/api/v1/patient/analyse", json={**PAY, "chief_complaint": "crushing chest pain"}).json()
    assert r["emergency"] and "emergency" in r["emergency_text"].lower()
    rid = r["recommendation_id"]
    body = {"rationale": "seen the escalation"}
    assert doc.post(f"/api/v1/recommendations/{rid}/review", json={**body, "action": "approve"}).status_code == 409
    assert doc.post(f"/api/v1/recommendations/{rid}/review", json={**body, "action": "acknowledge"}).status_code == 200


def test_input_validation_and_phi_guard(pat):
    assert pat.post("/api/v1/patient/analyse", json={**PAY, "notes": "call me on 555-123-4567"}).status_code == 422
    assert pat.post("/api/v1/patient/analyse", json={**PAY, "labs": {"egfr": -5}}).status_code == 422
    assert pat.post("/api/v1/patient/analyse", json={**PAY, "birth_date": "not-a-date"}).status_code == 422


def test_unit_bearing_labs_are_normalised(pat):
    r = pat.post("/api/v1/patient/analyse", json={**PAY, "labs": {"egfr": 24, "creatinine": {"value": 230, "unit": "umol/L"}}}).json()
    assert r["state_version"] == 1


def test_demo_timeline_rerun_and_audit(doc):
    d = doc.post("/api/v1/demo/seed?patient_id=DEMO-1").json()
    inv = [r["reasoning_invocations"] for r in d["recommendations"]]
    assert inv[0] == 4 and inv[1] == 0 and inv[2] >= 3                    # baseline, noise (reused), deterioration (re-plan)
    assert doc.post("/api/v1/demo/seed?patient_id=DEMO-1").status_code == 409
    tl = doc.get("/api/v1/patients/DEMO-1/timeline").json()
    assert tl["chain_verified"] and len(tl["versions"]) == 3
    full = doc.post("/api/v1/patients/DEMO-1/rerun?mode=full_rerun").json()
    assert full["agent_invocations"] == 8 and full["mode"] == "full_rerun"
    assert doc.post("/api/v1/patients/NOPE/rerun").status_code == 404
    assert doc.get("/api/v1/audit/verify").json()["chain_ok"]
    assert len(doc.get("/api/v1/recommendations").json()["items"]) == 4
    assert doc.get("/api/v1/system/status").json()["audit_chain_ok"] is True


def test_legacy_api_token_still_works_for_machine_access(env, pat):
    """The shared X-API-Token predates accounts. It is optional, off by default, and cannot pick a reviewer for you."""
    anon = TestClient(main.app)
    rid = pat.post("/api/v1/patient/analyse", json=PAY).json()["recommendation_id"]
    object.__setattr__(main.SET, "clinician_api_token", "s3cret")
    try:
        assert anon.get("/api/v1/recommendations").status_code == 401
        assert anon.get("/api/v1/recommendations", headers={"X-API-Token": "wrong"}).status_code == 401
        assert anon.get("/api/v1/recommendations", headers={"X-API-Token": "s3cret"}).status_code == 200
        h = {"X-API-Token": "s3cret"}
        assert anon.post(f"/api/v1/recommendations/{rid}/review", json={"action": "approve", "rationale": "reviewed ok"}, headers=h).status_code == 422   # needs a clinician_id
        ok = anon.post(f"/api/v1/recommendations/{rid}/review", json={"action": "approve", "rationale": "reviewed ok", "clinician_id": "DR-API"}, headers=h)
        assert ok.status_code == 200 and ok.json()["reviewed_by"] == "DR-API"
    finally:
        object.__setattr__(main.SET, "clinician_api_token", "")
    assert anon.get("/api/v1/recommendations", headers={"X-API-Token": "s3cret"}).status_code == 401                # token off => no access


def test_eval_results_endpoint_reports_missing_results_honestly(env, tmp_path):
    original = main.SET.results_dir
    object.__setattr__(main.SET, "results_dir", tmp_path)
    try:
        anon = TestClient(main.app)
        assert anon.get("/api/v1/eval/results").status_code == 404
        (tmp_path / "results.json").write_text(json.dumps({"meta": {}}))
        assert anon.get("/api/v1/eval/results").status_code == 200
    finally:
        object.__setattr__(main.SET, "results_dir", original)
