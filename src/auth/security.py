"""Password hashing, tokens and input validation for accounts (website layer).

Standard library only (hashlib.scrypt, secrets, hmac) so the prototype needs no new dependencies.

Design notes
- Passwords are stored as scrypt hashes with a per-password random salt. The hash string carries its own
  parameters, so the cost can be raised later without invalidating existing accounts.
- Session and reset tokens are random 256-bit values. Only their SHA-256 digest is stored, so a leaked
  database does not leak usable sessions.
- The password policy follows NIST SP 800-63B: length matters, composition rules do not. We require a
  minimum length, and reject very common passwords and passwords built from the account's own email/name.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import re
import secrets
from datetime import date, datetime
from typing import List, Optional

_N, _R, _P = 2 ** 14, 8, 1
_MAXMEM = 64 * 1024 * 1024

MIN_PASSWORD = 10
MAX_PASSWORD = 128

_COMMON = {
    "password", "password1", "password12", "password123", "passw0rd", "p@ssw0rd", "12345678", "123456789", "1234567890",
    "qwertyuiop", "qwerty123", "iloveyou12", "letmein123", "welcome123", "admin12345", "changeme123", "abc1234567",
    "1q2w3e4r5t", "0987654321", "111111111", "1111111111", "0000000000", "monkey1234", "dragon1234", "football123",
    "baseball123", "trustno1234", "sunshine123", "princess123", "starwars123", "whatever123", "passwordpassword",
}

_EMAIL = re.compile(r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]{1,64}@[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+$")
_STAFF_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_\-.]{2,40}$")
_CTRL = re.compile(r"[\x00-\x1f\x7f]")


def _b64(b: bytes) -> str:
    return base64.b64encode(b).decode("ascii")


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=_N, r=_R, p=_P, dklen=32, maxmem=_MAXMEM)
    return f"scrypt${_N}${_R}${_P}${_b64(salt)}${_b64(dk)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt, want = stored.split("$")
        if scheme != "scrypt":
            return False
        dk = hashlib.scrypt(password.encode("utf-8"), salt=base64.b64decode(salt), n=int(n), r=int(r), p=int(p),
                            dklen=len(base64.b64decode(want)), maxmem=_MAXMEM)
        return hmac.compare_digest(dk, base64.b64decode(want))
    except Exception:
        return False


# Verified against when the email is unknown, so "no such account" costs the same time as "wrong password".
DUMMY_HASH = hash_password(secrets.token_urlsafe(16))


def new_token(nbytes: int = 32) -> str:
    return secrets.token_urlsafe(nbytes)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def constant_time_equals(a: str, b: str) -> bool:
    return hmac.compare_digest((a or "").encode("utf-8"), (b or "").encode("utf-8"))


# ------------------------------------------------------------------ validation
def normalise_email(email: str) -> str:
    return (email or "").strip().lower()


def email_problem(email: str) -> Optional[str]:
    e = normalise_email(email)
    if not e or len(e) > 254 or not _EMAIL.match(e):
        return "Enter a valid email address, like name@example.com."
    return None


def name_problem(name: str) -> Optional[str]:
    n = (name or "").strip()
    if not n:
        return "Enter your name."
    if len(n) > 80 or _CTRL.search(n):
        return "Name must be 80 characters or fewer, with no control characters."
    return None


def staff_id_problem(staff_id: str) -> Optional[str]:
    if not _STAFF_ID.match(staff_id or ""):
        return "Staff ID must be 3-41 characters: letters, numbers, dot, dash or underscore."
    return None


def birth_date_problem(value: str) -> Optional[str]:
    try:
        d = datetime.fromisoformat(value or "").date()
    except ValueError:
        return "Enter a valid date of birth."
    if d > date.today() or d.year < 1900:
        return "Enter a valid date of birth."
    return None


def password_problems(password: str, email: str = "", name: str = "") -> List[str]:
    """Return a list of human-readable problems (empty list = acceptable)."""
    out: List[str] = []
    pw = password or ""
    if len(pw) < MIN_PASSWORD:
        out.append(f"Use at least {MIN_PASSWORD} characters.")
    if len(pw) > MAX_PASSWORD:
        out.append(f"Use no more than {MAX_PASSWORD} characters.")
    low = pw.lower()
    if low in _COMMON or (len(set(low)) < 4 and pw):
        out.append("That password is too easy to guess. Try a few unrelated words.")
    local = normalise_email(email).split("@")[0]
    if len(local) >= 4 and local in low:
        out.append("Don't include your email address in your password.")
    for part in re.split(r"\s+", (name or "").strip().lower()):
        if len(part) >= 4 and part in low:
            out.append("Don't include your name in your password.")
            break
    return out
