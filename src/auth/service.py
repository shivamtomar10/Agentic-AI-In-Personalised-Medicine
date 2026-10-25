"""Accounts and sessions for the website (patients and clinicians).

Rules enforced here (not in the browser):
- Two roles. Patients register themselves. Clinicians need the server's invite code, or are created by an
  administrator with `python run.py create-clinician`.
- A patient's `patient_id` is a random pseudonym generated on registration. It is the ONLY identifier that
  enters the clinical pipeline and the audit log; the email address never does.
- A clinician's `staff_id` is chosen at registration, unique, and is what gets written to the audit log as the
  reviewer. It comes from the signed-in account, never from the request body.
- Failed logins are counted per email address (whether or not the account exists) and lock further attempts.
- Sessions are server-side: the cookie holds a random token; the database holds only its SHA-256 digest.
"""
from __future__ import annotations

import json
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, Optional

from src.auth.db import Database
from src.auth.mailer import OutboxMailer
from src.auth.security import (DUMMY_HASH, birth_date_problem, constant_time_equals, email_problem, hash_password, hash_token,
                               name_problem, new_token, normalise_email, password_problems, staff_id_problem, verify_password)

GENDERS = ("male", "female", "other", "unknown")
MAX_FAILURES = 5
LOCK_MINUTES = 10
RESET_MINUTES = 30
MAX_SESSIONS_PER_USER = 5

# (idle timeout, absolute lifetime). Clinicians act on health records, so their sessions are shorter.
SESSION_POLICY = {"patient": (timedelta(hours=12), timedelta(days=7)), "clinician": (timedelta(minutes=30), timedelta(hours=12))}
_ID_ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"     # no 0/O/1/I/L: easier to read out


class AuthError(Exception):
    def __init__(self, message: str, status: int = 400, field: Optional[str] = None):
        super().__init__(message)
        self.message, self.status, self.field = message, status, field


@dataclass(frozen=True)
class User:
    id: int
    email: str
    role: str
    display_name: str
    patient_id: Optional[str]
    staff_id: Optional[str]
    birth_date: Optional[str]
    gender: Optional[str]
    created_at: str
    last_login_at: Optional[str]

    def public(self) -> Dict[str, Any]:
        return {"email": self.email, "role": self.role, "display_name": self.display_name, "patient_id": self.patient_id,
                "staff_id": self.staff_id, "birth_date": self.birth_date, "gender": self.gender,
                "created_at": self.created_at, "last_login_at": self.last_login_at}


@dataclass(frozen=True)
class Session:
    user: User
    csrf: str
    token_hash: str


def _row_user(r) -> User:
    return User(r["id"], r["email"], r["role"], r["display_name"], r["patient_id"], r["staff_id"], r["birth_date"], r["gender"],
                r["created_at"], r["last_login_at"])


class AuthService:
    def __init__(self, db: Database, mailer: Optional[OutboxMailer] = None, invite_code: str = "",
                 clock: Optional[Callable[[], datetime]] = None):
        self.db = db
        self.mailer = mailer or OutboxMailer()
        self.invite_code = invite_code
        self._now = clock or (lambda: datetime.now(timezone.utc))

    # ------------------------------------------------------------------ helpers
    def _iso(self, dt: Optional[datetime] = None) -> str:
        return (dt or self._now()).isoformat()

    @staticmethod
    def _parse(s: str) -> datetime:
        return datetime.fromisoformat(s)

    def _new_patient_id(self) -> str:
        while True:
            pid = "PT-" + "".join(secrets.choice(_ID_ALPHABET) for _ in range(8))
            if not self.db.one("SELECT 1 FROM users WHERE patient_id=?", (pid,)):
                return pid

    def _check_common(self, email: str, name: str, password: str, accept_terms: bool) -> str:
        e = normalise_email(email)
        for field, prob in (("email", email_problem(e)), ("display_name", name_problem(name))):
            if prob:
                raise AuthError(prob, 422, field)
        probs = password_problems(password, e, name)
        if probs:
            raise AuthError(" ".join(probs), 422, "password")
        if not accept_terms:
            raise AuthError("Please accept the Terms and Privacy Policy to create an account.", 422, "accept_terms")
        if self.db.one("SELECT 1 FROM users WHERE email=?", (e,)):
            raise AuthError("An account with this email already exists. Try signing in instead.", 409, "email")
        return e

    # ------------------------------------------------------------------ registration
    def register_patient(self, email: str, password: str, display_name: str, birth_date: str, gender: str, accept_terms: bool) -> User:
        e = self._check_common(email, display_name, password, accept_terms)
        if (p := birth_date_problem(birth_date)):
            raise AuthError(p, 422, "birth_date")
        if gender not in GENDERS:
            raise AuthError("Choose one of the listed options.", 422, "gender")
        now = self._iso()
        pid = self._new_patient_id()
        cur = self.db.execute(
            "INSERT INTO users (email, role, password_hash, display_name, patient_id, birth_date, gender, created_at, terms_accepted_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (e, "patient", hash_password(password), display_name.strip(), pid, birth_date, gender, now, now))
        return self.get_user(cur.lastrowid)  # type: ignore[arg-type]

    def register_clinician(self, email: str, password: str, display_name: str, staff_id: str, invite_code: str, accept_terms: bool,
                           *, skip_invite: bool = False) -> User:
        if not skip_invite:
            if not self.invite_code:
                raise AuthError("Clinician self-registration is switched off on this server. Ask an administrator to create your account.", 403, "invite_code")
            if not constant_time_equals((invite_code or "").strip(), self.invite_code):
                raise AuthError("That invite code isn't valid.", 403, "invite_code")
        if (p := staff_id_problem(staff_id)):
            raise AuthError(p, 422, "staff_id")
        e = self._check_common(email, display_name, password, accept_terms)
        if self.db.one("SELECT 1 FROM users WHERE lower(staff_id)=lower(?)", (staff_id,)):
            raise AuthError("That staff ID is already registered.", 409, "staff_id")
        now = self._iso()
        cur = self.db.execute(
            "INSERT INTO users (email, role, password_hash, display_name, staff_id, created_at, terms_accepted_at) VALUES (?,?,?,?,?,?,?)",
            (e, "clinician", hash_password(password), display_name.strip(), staff_id.strip(), now, now))
        return self.get_user(cur.lastrowid)  # type: ignore[arg-type]

    def get_user(self, user_id: int) -> User:
        r = self.db.one("SELECT * FROM users WHERE id=?", (user_id,))
        if r is None:
            raise AuthError("Account not found.", 404)
        return _row_user(r)

    def find_by_email(self, email: str) -> Optional[User]:
        r = self.db.one("SELECT * FROM users WHERE email=?", (normalise_email(email),))
        return _row_user(r) if r else None

    # ------------------------------------------------------------------ login
    def _attempt_key(self, email: str) -> str:
        return hash_token("login:" + normalise_email(email))[:32]

    def _locked_minutes(self, key: str) -> int:
        r = self.db.one("SELECT locked_until FROM login_attempts WHERE key=?", (key,))
        if r and r["locked_until"]:
            left = self._parse(r["locked_until"]) - self._now()
            if left.total_seconds() > 0:
                return max(1, int(left.total_seconds() // 60) + 1)
        return 0

    def _record_failure(self, key: str) -> None:
        now = self._now()
        r = self.db.one("SELECT failures, locked_until FROM login_attempts WHERE key=?", (key,))
        failures = (r["failures"] if r else 0) + 1
        locked = None
        if failures >= MAX_FAILURES:
            locked, failures = self._iso(now + timedelta(minutes=LOCK_MINUTES)), 0
        self.db.execute("INSERT INTO login_attempts (key, failures, locked_until, updated_at) VALUES (?,?,?,?) "
                        "ON CONFLICT(key) DO UPDATE SET failures=excluded.failures, locked_until=excluded.locked_until, updated_at=excluded.updated_at",
                        (key, failures, locked, self._iso(now)))

    def authenticate(self, email: str, password: str, role: str) -> User:
        """Check credentials for the chosen role. Every failure looks the same to the caller, except a correct
        password used on the wrong role tab (which reveals nothing to someone who doesn't already know it)."""
        key = self._attempt_key(email)
        mins = self._locked_minutes(key)
        if mins:
            raise AuthError(f"Too many failed sign-in attempts. Try again in about {mins} minute{'s' if mins != 1 else ''}.", 429)
        r = self.db.one("SELECT * FROM users WHERE email=?", (normalise_email(email),))
        ok = verify_password(password or "", r["password_hash"] if r else DUMMY_HASH)
        if not (r and ok) or r["disabled"]:
            self._record_failure(key)
            raise AuthError("Incorrect email or password.", 401)
        if r["role"] != role:
            other = "clinician" if r["role"] == "clinician" else "patient"
            raise AuthError(f"This is a {other} account. Use the {other.capitalize()} tab to sign in.", 403)
        self.db.execute("DELETE FROM login_attempts WHERE key=?", (key,))
        self.db.execute("UPDATE users SET last_login_at=? WHERE id=?", (self._iso(), r["id"]))
        return self.get_user(r["id"])

    # ------------------------------------------------------------------ sessions
    def create_session(self, user: User, user_agent: str = "") -> tuple[str, str]:
        token, csrf, now = new_token(), new_token(24), self._iso()
        self.db.execute("INSERT INTO sessions (token_hash, user_id, csrf, created_at, last_seen, user_agent) VALUES (?,?,?,?,?,?)",
                        (hash_token(token), user.id, csrf, now, now, (user_agent or "")[:200]))
        old = self.db.all("SELECT token_hash FROM sessions WHERE user_id=? ORDER BY created_at DESC", (user.id,))
        for row in old[MAX_SESSIONS_PER_USER:]:
            self.db.execute("DELETE FROM sessions WHERE token_hash=?", (row["token_hash"],))
        return token, csrf

    def session_lifetime_seconds(self, role: str) -> int:
        return int(SESSION_POLICY[role][1].total_seconds())

    def get_session(self, token: Optional[str]) -> Optional[Session]:
        if not token:
            return None
        th = hash_token(token)
        r = self.db.one("SELECT s.*, u.disabled AS udis FROM sessions s JOIN users u ON u.id = s.user_id WHERE s.token_hash=?", (th,))
        if r is None:
            return None
        user = self.get_user(r["user_id"])
        idle, absolute = SESSION_POLICY[user.role]
        now = self._now()
        if r["udis"] or now - self._parse(r["last_seen"]) > idle or now - self._parse(r["created_at"]) > absolute:
            self.db.execute("DELETE FROM sessions WHERE token_hash=?", (th,))
            return None
        if (now - self._parse(r["last_seen"])).total_seconds() > 30:
            self.db.execute("UPDATE sessions SET last_seen=? WHERE token_hash=?", (self._iso(now), th))
        return Session(user, r["csrf"], th)

    def destroy_session(self, token: Optional[str]) -> None:
        if token:
            self.db.execute("DELETE FROM sessions WHERE token_hash=?", (hash_token(token),))

    def destroy_user_sessions(self, user_id: int, keep_token_hash: Optional[str] = None) -> None:
        self.db.execute("DELETE FROM sessions WHERE user_id=? AND token_hash IS NOT ?", (user_id, keep_token_hash))

    # ------------------------------------------------------------------ password management
    def change_password(self, session: Session, current: str, new: str) -> None:
        r = self.db.one("SELECT * FROM users WHERE id=?", (session.user.id,))
        if r is None or not verify_password(current or "", r["password_hash"]):
            raise AuthError("Your current password isn't correct.", 403, "current_password")
        probs = password_problems(new, session.user.email, session.user.display_name)
        if probs:
            raise AuthError(" ".join(probs), 422, "new_password")
        if verify_password(new, r["password_hash"]):
            raise AuthError("Choose a password you haven't used for this account.", 422, "new_password")
        self.db.execute("UPDATE users SET password_hash=? WHERE id=?", (hash_password(new), session.user.id))
        self.destroy_user_sessions(session.user.id, keep_token_hash=session.token_hash)

    def start_reset(self, email: str, base_url: str) -> None:
        """Always returns quietly, so the response never reveals whether an account exists."""
        user = self.find_by_email(email)
        if user is None:
            return
        self.db.execute("UPDATE reset_tokens SET used=1 WHERE user_id=?", (user.id,))
        token = new_token()
        self.db.execute("INSERT INTO reset_tokens (token_hash, user_id, expires_at) VALUES (?,?,?)",
                        (hash_token(token), user.id, self._iso(self._now() + timedelta(minutes=RESET_MINUTES))))
        link = f"{base_url}/reset-password?token={token}"
        self.mailer.send(user.email, "Reset your password",
                         f"Hello {user.display_name},\n\nUse this link to choose a new password. It works once and expires in {RESET_MINUTES} minutes:\n\n{link}\n\n"
                         "If you didn't ask for this, ignore this email; your password hasn't changed.")

    def _valid_reset(self, token: str):
        r = self.db.one("SELECT * FROM reset_tokens WHERE token_hash=?", (hash_token(token or ""),))
        if r is None or r["used"] or self._parse(r["expires_at"]) < self._now():
            return None
        return r

    def reset_token_valid(self, token: str) -> bool:
        return self._valid_reset(token) is not None

    def finish_reset(self, token: str, new_password: str) -> User:
        r = self._valid_reset(token)
        if r is None:
            raise AuthError("This reset link has expired or was already used. Request a new one.", 400, "token")
        user = self.get_user(r["user_id"])
        probs = password_problems(new_password, user.email, user.display_name)
        if probs:
            raise AuthError(" ".join(probs), 422, "new_password")
        with self.db.transaction():
            self.db.execute("UPDATE users SET password_hash=? WHERE id=?", (hash_password(new_password), user.id))
            self.db.execute("UPDATE reset_tokens SET used=1 WHERE user_id=?", (user.id,))
            self.db.execute("DELETE FROM login_attempts WHERE key=?", (self._attempt_key(user.email),))
        self.destroy_user_sessions(user.id)
        return user

    # ------------------------------------------------------------------ patient intake memory
    def save_last_submission(self, user_id: int, submission: Dict[str, Any]) -> None:
        self.db.execute("UPDATE users SET last_submission=? WHERE id=?", (json.dumps(submission), user_id))

    def last_submission(self, user_id: int) -> Optional[Dict[str, Any]]:
        r = self.db.one("SELECT last_submission FROM users WHERE id=?", (user_id,))
        return json.loads(r["last_submission"]) if r and r["last_submission"] else None

    # ------------------------------------------------------------------ site messages
    def add_contact_message(self, name: str, email: str, topic: str, message: str) -> int:
        cur = self.db.execute("INSERT INTO contact_messages (name, email, topic, message, created_at) VALUES (?,?,?,?,?)",
                              (name.strip(), normalise_email(email), topic, message.strip(), self._iso()))
        return int(cur.lastrowid)  # type: ignore[arg-type]

    # ------------------------------------------------------------------ care team assignments & account erasure
    def assign_patient(self, clinician_id: int, patient_id: str) -> None:
        """Assigns a patient to a clinician's care team."""
        self.db.execute(
            "INSERT INTO patient_assignments (clinician_id, patient_id, assigned_at) VALUES (?,?,?) "
            "ON CONFLICT(clinician_id, patient_id) DO NOTHING",
            (clinician_id, patient_id, self._iso())
        )

    def unassign_patient(self, clinician_id: int, patient_id: str) -> None:
        """Removes a patient from a clinician's care team."""
        self.db.execute("DELETE FROM patient_assignments WHERE clinician_id=? AND patient_id=?", (clinician_id, patient_id))

    def get_assigned_patients(self, clinician_id: int) -> list[str]:
        """Returns list of patient_ids assigned to the given clinician."""
        rows = self.db.all("SELECT patient_id FROM patient_assignments WHERE clinician_id=?", (clinician_id,))
        return [r["patient_id"] for r in rows]

    def is_patient_assigned(self, clinician_id: int, patient_id: str) -> bool:
        """Returns True if the clinician is explicitly assigned to this patient."""
        r = self.db.one("SELECT 1 FROM patient_assignments WHERE clinician_id=? AND patient_id=?", (clinician_id, patient_id))
        return r is not None

    def delete_account(self, user_id: int) -> bool:
        """Permanently deletes a user account and purges associated sessions (GDPR erasure)."""
        with self.db.transaction():
            self.db.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))
            self.db.execute("DELETE FROM reset_tokens WHERE user_id=?", (user_id,))
            self.db.execute("DELETE FROM patient_assignments WHERE clinician_id=?", (user_id,))
            cur = self.db.execute("DELETE FROM users WHERE id=?", (user_id,))
            return cur.rowcount > 0

