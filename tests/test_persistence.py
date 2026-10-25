"""Accounts and clinical records survive a server restart (simulated by building a new context on the same files)."""
from src.api import main
from tests.helpers import Actor

PAY = {"conditions": ["Type 2 diabetes (E11.9)"], "medications": ["Metformin 1000 mg BID"], "allergies": [], "labs": {"egfr": 24, "potassium": 5.3}}


def boot(tmp_path):
    main.reset_context(str(tmp_path / "audit.jsonl"), db_path=tmp_path / "app.db", state_path=tmp_path / "states.jsonl", persist=True, invite_code="INVITE-1")


def test_everything_important_survives_a_restart(tmp_path):
    boot(tmp_path)
    p, d = Actor(), Actor()
    p.register_patient()
    d.register_clinician()
    rid1 = p.post("/api/v1/patient/analyse", json=PAY).json()["recommendation_id"]
    rid2 = p.post("/api/v1/patient/analyse", json={**PAY, "labs": {"egfr": 23.5, "potassium": 5.3}}).json()["recommendation_id"]
    d.post(f"/api/v1/recommendations/{rid1}/review", json={"action": "approve", "rationale": "reviewed and agree"})
    pid = p.patient_id

    boot(tmp_path)                                                                   # ---- restart ----
    p2, d2 = Actor(), Actor()
    assert p2.login("pat@example.com", "patient").status_code == 200                # accounts persisted
    assert d2.login("dr@example.com", "clinician").status_code == 200
    assert p2.patient_id == pid
    items = p2.get("/api/v1/patient/me/recommendations").json()["items"]
    assert [i["recommendation_id"] for i in items] == [rid2, rid1]                  # newest first, order preserved
    assert p2.get(f"/api/v1/patient/status/{rid1}").json()["reviewed_by"] == "DR-TEST"   # the clinician's decision persisted
    assert p2.get("/api/v1/patient/me/last-submission").json()["submission"]["medications"] == ["Metformin 1000 mg BID"]
    assert d2.get(f"/api/v1/patients/{pid}/timeline").json()["chain_verified"] is True
    assert main.ctx.audit.verify_chain()

    # the next check-in continues the same longitudinal record (agent cache is gone, so all agents simply re-run)
    r3 = p2.post("/api/v1/patient/analyse", json={**PAY, "labs": {"egfr": 23.0, "potassium": 5.3}}).json()
    assert r3["state_version"] == 3 and r3["agent_invocations"] == 8
    assert d2.get(f"/api/v1/patients/{pid}/timeline").json()["chain_verified"] is True
    assert len(d2.get("/api/v1/recommendations").json()["items"]) == 3


def test_sessions_also_survive_a_restart(tmp_path):
    boot(tmp_path)
    p = Actor()
    p.register_patient()
    boot(tmp_path)
    assert p.get("/api/auth/me").json()["authenticated"] is True                      # same cookie still valid against the persisted session table


def test_audit_chain_survives_two_writers_on_one_file(tmp_path):
    """Two logger instances (e.g. two server workers) appending to the same file must not fork the hash chain."""
    from src.audit.logger import AuditLogger
    path = str(tmp_path / "audit.jsonl")
    a, b = AuditLogger(path), AuditLogger(path)
    for i in range(6):
        (a if i % 2 == 0 else b).log_event("e", {"i": i})
    assert a.verify_chain() and len(a.read_all()) == 6
