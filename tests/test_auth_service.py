"""Accounts, sessions, lockout, reset - the rules the website's login depends on."""
from datetime import datetime, timedelta, timezone

import pytest

from src.auth import AuthError, AuthService, Database, OutboxMailer
from src.auth.security import hash_password, password_problems, verify_password

PW = "correct horse battery"


class Clock:
    def __init__(self):
        self.t = datetime(2026, 9, 21, 12, 0, tzinfo=timezone.utc)

    def __call__(self):
        return self.t

    def advance(self, **kw):
        self.t += timedelta(**kw)


@pytest.fixture()
def svc():
    clock = Clock()
    s = AuthService(Database(":memory:"), OutboxMailer(), invite_code="INVITE-1", clock=clock)
    s.clock = clock
    return s


def patient(svc, email="pat@example.com", pw=PW):
    return svc.register_patient(email, pw, "Pat Example", "1970-05-05", "female", True)


def test_password_hash_roundtrip_and_salting():
    a, b = hash_password("s3cret-passphrase"), hash_password("s3cret-passphrase")
    assert a != b and a.startswith("scrypt$")            # random salt: same password, different hash
    assert verify_password("s3cret-passphrase", a) and not verify_password("wrong", a)
    assert not verify_password("x", "garbage") and not verify_password("x", "")


def test_password_policy():
    assert password_problems("short") and password_problems("password123")
    assert password_problems("aaaaaaaaaaaa")
    assert password_problems("jane.doe.2026!", email="jane.doe@example.com")          # contains email local part
    assert password_problems("maryjane-rocks-2026", name="Mary Jane")
    assert password_problems("purple-otter-lantern") == []                            # length, no composition rules


def test_register_patient_assigns_pseudonymous_id_and_hides_hash(svc):
    u = patient(svc)
    assert u.role == "patient" and u.patient_id.startswith("PT-") and len(u.patient_id) == 11
    assert "password" not in str(u.public()).lower() and svc.find_by_email("PAT@Example.com").id == u.id


@pytest.mark.parametrize("kw,field", [
    (dict(email="nope"), "email"), (dict(pw="short"), "password"), (dict(birth="2999-01-01"), "birth_date"),
    (dict(gender="x"), "gender"), (dict(terms=False), "accept_terms"), (dict(name="  "), "display_name")])
def test_register_patient_validation(svc, kw, field):
    args = dict(email="a@example.com", pw=PW, name="A B", birth="1980-01-01", gender="male", terms=True) | kw
    with pytest.raises(AuthError) as e:
        svc.register_patient(args["email"], args["pw"], args["name"], args["birth"], args["gender"], args["terms"])
    assert e.value.field == field and e.value.status in (422,)


def test_duplicate_email_rejected_case_insensitively(svc):
    patient(svc)
    with pytest.raises(AuthError) as e:
        patient(svc, email="PAT@example.com")
    assert e.value.status == 409 and e.value.field == "email"


def test_clinician_needs_invite_and_unique_staff_id(svc):
    with pytest.raises(AuthError) as e:
        svc.register_clinician("dr@example.com", PW, "Dr A", "DR-A", "WRONG", True)
    assert e.value.status == 403
    u = svc.register_clinician("dr@example.com", PW, "Dr A", "DR-A", "INVITE-1", True)
    assert u.role == "clinician" and u.staff_id == "DR-A" and u.patient_id is None
    with pytest.raises(AuthError) as e2:
        svc.register_clinician("dr2@example.com", PW, "Dr B", "dr-a", "INVITE-1", True)
    assert e2.value.field == "staff_id"
    off = AuthService(Database(":memory:"), invite_code="")                            # disabled unless configured
    with pytest.raises(AuthError):
        off.register_clinician("x@example.com", PW, "X", "DR-X", "", True)
    ok = off.register_clinician("x@example.com", PW, "X Y", "DR-X", "", True, skip_invite=True)   # admin CLI path
    assert ok.role == "clinician"


def test_authenticate_checks_password_and_role(svc):
    patient(svc)
    assert svc.authenticate("pat@example.com", PW, "patient").email == "pat@example.com"
    with pytest.raises(AuthError) as bad:
        svc.authenticate("pat@example.com", "wrong-password", "patient")
    with pytest.raises(AuthError) as none:
        svc.authenticate("ghost@example.com", "wrong-password", "patient")
    assert bad.value.message == none.value.message == "Incorrect email or password." and bad.value.status == 401   # no user enumeration
    with pytest.raises(AuthError) as wrongtab:
        svc.authenticate("pat@example.com", PW, "clinician")
    assert wrongtab.value.status == 403 and "patient account" in wrongtab.value.message


def test_lockout_after_repeated_failures_then_expires(svc):
    patient(svc)
    for _ in range(5):
        with pytest.raises(AuthError):
            svc.authenticate("pat@example.com", "nope-nope-nope", "patient")
    with pytest.raises(AuthError) as e:
        svc.authenticate("pat@example.com", PW, "patient")            # even the right password is refused while locked
    assert e.value.status == 429
    svc.clock.advance(minutes=11)
    assert svc.authenticate("pat@example.com", PW, "patient")
    for _ in range(5):                                                # unknown emails are throttled the same way
        with pytest.raises(AuthError):
            svc.authenticate("ghost@example.com", "x" * 12, "patient")
    with pytest.raises(AuthError) as g:
        svc.authenticate("ghost@example.com", "x" * 12, "patient")
    assert g.value.status == 429


def test_session_lifecycle_idle_and_absolute_expiry(svc):
    u = patient(svc)
    tok, csrf = svc.create_session(u)
    s = svc.get_session(tok)
    assert s and s.user.id == u.id and s.csrf == csrf
    assert svc.db.one("SELECT token_hash FROM sessions").keys() and tok not in str(svc.db.all("SELECT * FROM sessions")[0][0])  # digest stored, not token
    svc.clock.advance(hours=11)
    assert svc.get_session(tok)                                       # active use keeps a patient signed in
    svc.clock.advance(hours=13)
    assert svc.get_session(tok) is None                               # idle > 12h
    svc.destroy_session(tok)
    assert svc.get_session(None) is None and svc.get_session("bogus") is None


def test_clinician_sessions_time_out_sooner(svc):
    c = svc.register_clinician("dr@example.com", PW, "Dr A", "DR-A", "INVITE-1", True)
    tok, _ = svc.create_session(c)
    svc.clock.advance(minutes=29)
    assert svc.get_session(tok)
    svc.clock.advance(minutes=31)
    assert svc.get_session(tok) is None


def test_logout_and_session_cap(svc):
    u = patient(svc)
    toks = [svc.create_session(u)[0] for _ in range(7)]
    live = [t for t in toks if svc.get_session(t)]
    assert len(live) == 5


def test_change_password_revokes_other_sessions(svc):
    u = patient(svc)
    t1, _ = svc.create_session(u)
    t2, _ = svc.create_session(u)
    s2 = svc.get_session(t2)
    with pytest.raises(AuthError):
        svc.change_password(s2, "wrong-current", "a-brand-new-passphrase")
    with pytest.raises(AuthError):
        svc.change_password(s2, PW, "short")
    svc.change_password(s2, PW, "a-brand-new-passphrase")
    assert svc.get_session(t1) is None and svc.get_session(t2)
    assert svc.authenticate("pat@example.com", "a-brand-new-passphrase", "patient")


def test_password_reset_flow(svc):
    u = patient(svc)
    tok, _ = svc.create_session(u)
    svc.start_reset("ghost@example.com", "http://x")                  # unknown email: silent, sends nothing
    assert not svc.mailer.sent
    svc.start_reset("pat@example.com", "http://x")
    body = svc.mailer.sent[-1]["body"]
    token = body.split("token=")[1].split()[0]
    assert svc.reset_token_valid(token) and not svc.reset_token_valid("nope")
    with pytest.raises(AuthError):
        svc.finish_reset(token, "short")
    svc.finish_reset(token, "another-long-passphrase")
    assert svc.get_session(tok) is None                               # all sessions revoked
    with pytest.raises(AuthError):
        svc.finish_reset(token, "yet-another-passphrase")             # single use
    assert svc.authenticate("pat@example.com", "another-long-passphrase", "patient")
    svc.start_reset("pat@example.com", "http://x")
    t2 = svc.mailer.sent[-1]["body"].split("token=")[1].split()[0]
    svc.clock.advance(minutes=31)
    with pytest.raises(AuthError):
        svc.finish_reset(t2, "expired-token-passphrase")


def test_last_submission_roundtrip(svc):
    u = patient(svc)
    assert svc.last_submission(u.id) is None
    svc.save_last_submission(u.id, {"medications": ["Metformin 500 mg"]})
    assert svc.last_submission(u.id) == {"medications": ["Metformin 500 mg"]}
