"""/api/auth/*, /api/contact and /api/site/config.

Every unsafe request from a signed-in browser must carry the X-CSRF-Token issued at sign-in (see deps.py).
Sign-in, registration and password-reset requests come from signed-out browsers and are protected by the
SameSite=Lax cookie, the same-origin check in pages.py, and per-IP rate limits.
"""
from __future__ import annotations

from typing import Any, Dict, Literal, Optional

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field

from src.api.context import ctx
from src.api.deps import COOKIE, enforce_rate, require_session, session_optional
from src.auth import Session
from src.auth.security import email_problem
from src.config import get_settings
from src.safety.phi import find_phi

SET = get_settings()
router = APIRouter(prefix="/api")

DEMO_PATIENT = {"email": "demo.patient@example.com", "password": "Harbor-Lantern-2026"}
DEMO_CLINICIAN = {"email": "demo.clinician@example.com", "password": "Copper-Meadow-2026"}


class PatientRegistration(BaseModel):
    email: str = Field(..., max_length=254)
    password: str = Field(..., max_length=200)
    display_name: str = Field(..., max_length=120)
    birth_date: str = Field(..., max_length=10)
    gender: str = Field(..., max_length=10)
    accept_terms: bool = False


class ClinicianRegistration(BaseModel):
    email: str = Field(..., max_length=254)
    password: str = Field(..., max_length=200)
    display_name: str = Field(..., max_length=120)
    staff_id: str = Field(..., max_length=60)
    invite_code: str = Field(default="", max_length=120)
    accept_terms: bool = False


class Login(BaseModel):
    email: str = Field(..., max_length=254)
    password: str = Field(..., max_length=200)
    role: Literal["patient", "clinician"]


class ChangePassword(BaseModel):
    current_password: str = Field(..., max_length=200)
    new_password: str = Field(..., max_length=200)


class ForgotPassword(BaseModel):
    email: str = Field(..., max_length=254)


class ResetPassword(BaseModel):
    token: str = Field(..., max_length=200)
    new_password: str = Field(..., max_length=200)


class ContactIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=80)
    email: str = Field(..., max_length=254)
    topic: Literal["general", "account", "clinician-access", "research", "accessibility", "problem"] = "general"
    message: str = Field(..., min_length=10, max_length=2000)
    website: str = Field(default="", max_length=200)      # honeypot: real people never see or fill this


def _home_for(role: str) -> str:
    return "/clinician" if role == "clinician" else "/portal"


def _start_session(request: Request, response: Response, user) -> Dict[str, Any]:
    token, csrf = ctx.auth.create_session(user, request.headers.get("user-agent", ""))
    response.set_cookie(COOKIE, token, max_age=ctx.auth.session_lifetime_seconds(user.role), httponly=True, samesite="lax",
                        secure=SET.cookie_secure, path="/")
    return {"user": user.public(), "csrf": csrf, "redirect": _home_for(user.role)}


@router.post("/auth/register", status_code=201)
def register_patient(body: PatientRegistration, request: Request, response: Response) -> Dict[str, Any]:
    enforce_rate(request, "register", 10, 3600)
    user = ctx.auth.register_patient(body.email, body.password, body.display_name, body.birth_date, body.gender, body.accept_terms)
    ctx.audit.log_event("account_created", {"role": "patient", "patient_id": user.patient_id})
    return _start_session(request, response, user)


@router.post("/auth/register-clinician", status_code=201)
def register_clinician(body: ClinicianRegistration, request: Request, response: Response) -> Dict[str, Any]:
    enforce_rate(request, "register", 10, 3600)
    user = ctx.auth.register_clinician(body.email, body.password, body.display_name, body.staff_id, body.invite_code, body.accept_terms)
    ctx.audit.log_event("account_created", {"role": "clinician", "staff_id": user.staff_id})
    return _start_session(request, response, user)


@router.post("/auth/login")
def login(body: Login, request: Request, response: Response) -> Dict[str, Any]:
    enforce_rate(request, "login", 30, 600)
    try:
        user = ctx.auth.authenticate(body.email, body.password, body.role)
    except Exception as e:
        ctx.audit.log_event("login_failed", {"role": body.role})
        raise e
    ctx.audit.log_event("login", {"role": user.role, "who": user.staff_id or user.patient_id})
    return _start_session(request, response, user)


@router.post("/auth/logout")
def logout(request: Request, response: Response) -> Dict[str, Any]:
    sess = session_optional(request)
    if sess is not None:
        require_session(request)                              # CSRF-checked
        ctx.auth.destroy_session(request.cookies.get(COOKIE))
        ctx.audit.log_event("logout", {"role": sess.user.role, "who": sess.user.staff_id or sess.user.patient_id})
    response.delete_cookie(COOKIE, path="/")
    return {"ok": True}


@router.get("/auth/me")
def me(request: Request) -> Dict[str, Any]:
    sess = session_optional(request)
    if sess is None:
        return {"authenticated": False, "user": None, "csrf": None}
    return {"authenticated": True, "user": sess.user.public(), "csrf": sess.csrf}


@router.post("/auth/change-password")
def change_password(body: ChangePassword, sess: Session = Depends(require_session)) -> Dict[str, Any]:
    ctx.auth.change_password(sess, body.current_password, body.new_password)
    ctx.audit.log_event("password_changed", {"role": sess.user.role, "who": sess.user.staff_id or sess.user.patient_id})
    return {"ok": True, "message": "Password changed. Other devices have been signed out."}


@router.post("/auth/forgot-password")
def forgot_password(body: ForgotPassword, request: Request) -> Dict[str, Any]:
    enforce_rate(request, "forgot", 5, 3600)
    if not email_problem(body.email):
        base = SET.public_url or str(request.base_url).rstrip("/")
        ctx.auth.start_reset(body.email, base)
    # Same answer whether or not the address has an account.
    return {"ok": True, "message": "If that email has an account, we've sent a link to reset the password. It expires in 30 minutes."}


@router.get("/auth/reset-password/check")
def reset_check(token: str) -> Dict[str, Any]:
    return {"valid": ctx.auth.reset_token_valid(token)}


@router.post("/auth/reset-password")
def reset_password(body: ResetPassword, request: Request) -> Dict[str, Any]:
    enforce_rate(request, "reset", 10, 3600)
    user = ctx.auth.finish_reset(body.token, body.new_password)
    ctx.audit.log_event("password_reset", {"role": user.role, "who": user.staff_id or user.patient_id})
    return {"ok": True, "message": "Password updated. You can sign in with it now."}


@router.post("/contact", status_code=201)
def contact(body: ContactIn, request: Request) -> Dict[str, Any]:
    enforce_rate(request, "contact", 5, 3600)
    if body.website:                                           # honeypot tripped: pretend success, store nothing
        return {"ok": True, "reference": "MSG-0"}
    from src.auth.service import AuthError
    if (p := email_problem(body.email)):
        raise AuthError(p, 422, "email")
    if find_phi([body.message]):
        raise AuthError("Please remove phone numbers, ID numbers or email addresses from the message. "
                        "For your privacy, contact details should not be included in the message body.", 422, "message")
    mid = ctx.auth.add_contact_message(body.name, body.email, body.topic, body.message)
    return {"ok": True, "reference": f"MSG-{mid:05d}"}


@router.get("/site/config")
def site_config() -> Dict[str, Any]:
    out: Dict[str, Any] = {"site_name": SET.site_name, "demo_mode": SET.demo_mode, "clinician_signup_enabled": bool(SET.clinician_invite_code)}
    if SET.demo_mode:                                          # never true by default in production
        out["demo_accounts"] = {"patient": DEMO_PATIENT, "clinician": DEMO_CLINICIAN, "clinician_invite_code": SET.clinician_invite_code}
    return out
