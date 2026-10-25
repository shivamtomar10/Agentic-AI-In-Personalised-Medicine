"""Request dependencies: who is signed in, CSRF, roles, rate limits."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

from fastapi import Depends, Header, HTTPException, Request

from src.auth import Session
from src.config import get_settings
from src.api.context import ctx

COOKIE = "cdss_session"
_SAFE = {"GET", "HEAD", "OPTIONS"}
_UNSET = object()


def client_ip(request: Request) -> str:
    if get_settings().trust_proxy:
        fwd = request.headers.get("x-forwarded-for")
        if fwd:
            return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def enforce_rate(request: Request, name: str, limit: int, window_s: float) -> None:
    if not ctx.limiter.allow(f"{name}:{client_ip(request)}", limit, window_s):
        raise HTTPException(status_code=429, detail="Too many requests. Please wait a few minutes and try again.")


def session_optional(request: Request) -> Optional[Session]:
    cached = getattr(request.state, "session_cache", _UNSET)
    if cached is _UNSET:
        cached = ctx.auth.get_session(request.cookies.get(COOKIE))
        request.state.session_cache = cached
    return cached


def _csrf_ok(request: Request, sess: Session) -> bool:
    if request.method in _SAFE:
        return True
    from src.auth.security import constant_time_equals
    return constant_time_equals(request.headers.get("x-csrf-token", ""), sess.csrf)


def require_session(request: Request) -> Session:
    sess = session_optional(request)
    if sess is None:
        raise HTTPException(status_code=401, detail="Please sign in to continue.")
    if not _csrf_ok(request, sess):
        raise HTTPException(status_code=403, detail="Your security token is missing or out of date. Reload the page and try again.")
    return sess


def require_role(role: str) -> Callable[..., Session]:
    def dep(sess: Session = Depends(require_session)) -> Session:
        if sess.user.role != role:
            raise HTTPException(status_code=403, detail=f"This area is for {role}s.")
        return sess
    return dep


require_patient = require_role("patient")


@dataclass(frozen=True)
class Clinician:
    """Who is acting as a clinician: a signed-in clinician account, or (machine access) the shared API token."""
    staff_id: Optional[str]
    display_name: Optional[str]
    via: str                                   # 'session' | 'token'


def require_clinician(request: Request, x_api_token: Optional[str] = Header(default=None)) -> Clinician:
    sess = session_optional(request)
    if sess is not None:
        if sess.user.role != "clinician":
            raise HTTPException(status_code=403, detail="This area is for clinicians.")
        require_session(request)               # CSRF check on state-changing calls
        return Clinician(sess.user.staff_id, sess.user.display_name, "session")
    tok = get_settings().clinician_api_token
    if tok and x_api_token == tok:
        return Clinician(None, None, "token")
    raise HTTPException(status_code=401, detail="Clinician sign-in required.")
