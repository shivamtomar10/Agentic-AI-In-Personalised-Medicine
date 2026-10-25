"""The website's sign-in system, tested through the real HTTP API."""
import pytest
from fastapi.testclient import TestClient

from src.api import main
from src.api.deps import COOKIE
from tests.helpers import PW, Actor


@pytest.fixture()
def env(tmp_path):
    main.reset_context(str(tmp_path / "audit.jsonl"), invite_code="INVITE-1")


def anon():
    return TestClient(main.app)


def test_register_signs_you_in_with_a_hardened_cookie(env):
    a = Actor()
    r = a.register_patient()
    assert r.status_code == 201 and r.json()["redirect"] == "/portal" and r.json()["user"]["patient_id"].startswith("PT-")
    cookie = r.headers["set-cookie"].lower()
    assert COOKIE in cookie and "httponly" in cookie and "samesite=lax" in cookie
    assert "password" not in r.text.lower()
    me = a.get("/api/auth/me").json()
    assert me["authenticated"] and me["user"]["email"] == "pat@example.com" and me["csrf"] == a.csrf


def test_me_when_signed_out(env):
    assert anon().get("/api/auth/me").json() == {"authenticated": False, "user": None, "csrf": None}


def test_login_logout_roundtrip(env):
    Actor().register_patient()
    a = Actor()
    assert a.login("pat@example.com", "patient", "wrong-password").status_code == 401
    assert a.login("pat@example.com", "patient").status_code == 200
    assert a.get("/api/auth/me").json()["authenticated"] is True
    assert a.post("/api/auth/logout").status_code == 200
    assert a.get("/api/auth/me").json()["authenticated"] is False
    assert a.get("/api/v1/patient/me/recommendations").status_code == 401


def test_same_error_for_unknown_email_and_wrong_password(env):
    Actor().register_patient()
    a = Actor().login("pat@example.com", "patient", "not-the-password")
    b = Actor().login("ghost@example.com", "patient", "not-the-password")
    assert a.status_code == b.status_code == 401 and a.json() == b.json()


def test_roles_are_separate_areas(env):
    p, d = Actor(), Actor()
    p.register_patient()
    d.register_clinician()
    assert p.get("/api/v1/recommendations").status_code == 403                     # patient can't read the review queue
    assert p.post("/api/v1/recommendations/X/review", json={"action": "approve", "rationale": "nope nope"}).status_code == 403
    assert d.get("/api/v1/patient/me/recommendations").status_code == 403           # clinician isn't a patient
    assert d.post("/api/v1/patient/analyse", json={}).status_code == 403
    assert anon().get("/api/v1/recommendations").status_code == 401
    wrong_tab = Actor().login("dr@example.com", "patient")
    assert wrong_tab.status_code == 403 and "clinician account" in wrong_tab.json()["detail"]


def test_csrf_token_required_for_signed_in_writes(env):
    a = Actor()
    a.register_patient()
    body = {"conditions": [], "medications": [], "allergies": [], "labs": {"egfr": 60}}
    assert a.c.post("/api/v1/patient/analyse", json=body).status_code == 403                                   # cookie alone isn't enough
    assert a.c.post("/api/v1/patient/analyse", json=body, headers={"X-CSRF-Token": "forged"}).status_code == 403
    assert a.post("/api/v1/patient/analyse", json=body).status_code == 200
    assert a.c.post("/api/auth/logout").status_code == 403                                                     # even sign-out is CSRF-checked


def test_cross_site_origin_is_blocked(env):
    r = anon().post("/api/auth/login", json={"email": "a@example.com", "password": "x" * 12, "role": "patient"}, headers={"Origin": "https://evil.example"})
    assert r.status_code == 403
    same = anon().post("/api/auth/login", json={"email": "a@example.com", "password": "x" * 12, "role": "patient"}, headers={"Origin": "http://testserver"})
    assert same.status_code == 401


def test_clinician_registration_needs_the_invite_code(env):
    a = Actor()
    r = a.register_clinician(invite="WRONG")
    assert r.status_code == 403 and r.json()["field"] == "invite_code"
    assert a.register_clinician().status_code == 201
    assert Actor().register_clinician(email="two@example.com", staff="DR-TEST").json()["field"] == "staff_id"


def test_registration_errors_name_the_field(env):
    a = Actor()
    assert a.register_patient(pw="short").json()["field"] == "password"
    assert a.register_patient(email="bad").json()["field"] == "email"
    assert a.register_patient(birth="2999-01-01").json()["field"] == "birth_date"
    assert a.register_patient().status_code == 201
    dup = Actor().register_patient()
    assert dup.status_code == 409 and dup.json()["field"] == "email"


def test_lockout_over_http(env):
    Actor().register_patient()
    for _ in range(5):
        assert Actor().login("pat@example.com", "patient", "wrong-password").status_code == 401
    r = Actor().login("pat@example.com", "patient")
    assert r.status_code == 429 and "Try again" in r.json()["detail"]


def test_change_password_and_other_devices_signed_out(env):
    a, b = Actor(), Actor()
    a.register_patient()
    b.login("pat@example.com", "patient")
    assert a.post("/api/auth/change-password", json={"current_password": "nope-nope-nope", "new_password": "a-brand-new-passphrase"}).status_code == 403
    assert a.post("/api/auth/change-password", json={"current_password": PW, "new_password": "a-brand-new-passphrase"}).status_code == 200
    assert b.get("/api/auth/me").json()["authenticated"] is False        # the other device is signed out
    assert a.get("/api/auth/me").json()["authenticated"] is True
    assert Actor().login("pat@example.com", "patient", "a-brand-new-passphrase").status_code == 200


def test_forgot_and_reset_password_flow(env):
    Actor().register_patient()
    c = anon()
    r1 = c.post("/api/auth/forgot-password", json={"email": "pat@example.com"})
    r2 = c.post("/api/auth/forgot-password", json={"email": "ghost@example.com"})
    assert r1.status_code == r2.status_code == 200 and r1.json() == r2.json()      # no account enumeration
    sent = main.ctx.mailer.sent
    assert len(sent) == 1 and sent[0]["to"] == "pat@example.com"
    token = sent[0]["body"].split("token=")[1].split()[0]
    assert c.get(f"/api/auth/reset-password/check?token={token}").json() == {"valid": True}
    assert c.post("/api/auth/reset-password", json={"token": token, "new_password": "short"}).status_code == 422
    assert c.post("/api/auth/reset-password", json={"token": token, "new_password": "fresh-start-passphrase"}).status_code == 200
    assert c.post("/api/auth/reset-password", json={"token": token, "new_password": "fresh-start-passphrase"}).status_code == 400
    assert Actor().login("pat@example.com", "patient", "fresh-start-passphrase").status_code == 200
    assert Actor().login("pat@example.com", "patient", PW).status_code == 401


def test_contact_form(env):
    c = anon()
    ok = c.post("/api/contact", json={"name": "Sam", "email": "sam@example.com", "topic": "general", "message": "Hello, I have a question about the site."})
    assert ok.status_code == 201 and ok.json()["reference"].startswith("MSG-")
    assert main.ctx.db.one("SELECT COUNT(*) AS n FROM contact_messages")["n"] == 1
    phi = c.post("/api/contact", json={"name": "Sam", "email": "sam@example.com", "message": "Call me on 555-123-4567 about my results"})
    assert phi.status_code == 422 and phi.json()["field"] == "message"
    bot = c.post("/api/contact", json={"name": "Bot", "email": "bot@example.com", "message": "Buy cheap pills now please", "website": "http://spam"})
    assert bot.status_code == 201 and main.ctx.db.one("SELECT COUNT(*) AS n FROM contact_messages")["n"] == 1      # honeypot: nothing stored
    assert c.post("/api/contact", json={"name": "Sam", "email": "not-an-email", "message": "Hello there, long enough."}).status_code == 422


def test_rate_limits(env):
    c = anon()
    codes = [c.post("/api/auth/forgot-password", json={"email": "x@example.com"}).status_code for _ in range(7)]
    assert codes[:5] == [200] * 5 and codes[5:] == [429, 429]


def test_site_config_exposes_demo_accounts_only_in_demo_mode(env):
    on = anon().get("/api/site/config").json()
    assert on["demo_mode"] is True and on["demo_accounts"]["patient"]["email"]
    object.__setattr__(main.SET, "demo_mode", False)
    try:
        off = anon().get("/api/site/config").json()
        assert "demo_accounts" not in off and off["demo_mode"] is False
        assert Actor().register_clinician().status_code == 201 and Actor().register_patient().status_code == 201
        d = Actor(); d.register_clinician(email="d2@example.com", staff="DR-TWO")
        assert d.post("/api/v1/demo/seed").status_code == 404                        # demo data endpoint off outside demo mode
    finally:
        object.__setattr__(main.SET, "demo_mode", True)


def test_auth_events_are_audited_without_email_addresses(env):
    a = Actor()
    a.register_patient()
    a.post("/api/auth/logout")
    Actor().login("pat@example.com", "patient", "wrong-password")
    events = main.ctx.audit.read_all()
    assert {"account_created", "logout", "login_failed"} <= {e["event"] for e in events}
    assert "pat@example.com" not in str(events) and main.ctx.audit.verify_chain()


def test_demo_accounts_are_seeded_and_usable(env):
    main.seed_demo_accounts()
    main.seed_demo_accounts()                                                        # idempotent
    p, d = Actor(), Actor()
    assert p.login("demo.patient@example.com", "patient", "Harbor-Lantern-2026").status_code == 200
    assert d.login("demo.clinician@example.com", "clinician", "Copper-Meadow-2026").status_code == 200
    assert len(p.get("/api/v1/patient/me/recommendations").json()["items"]) == 3
    assert p.get("/api/v1/patient/me/last-submission").json()["submission"]["medications"]
    q = d.get("/api/v1/recommendations").json()
    assert q["count"] == 3 and d.get("/api/v1/patients").json()["patients"][0]["awaiting_review"] == 3
