"""FastAPI service: the website, accounts, patient check-ins and the clinician review workflow, on the REAL pipeline.

Governance (AGENTS.md): every recommendation requires clinician review; approvals need a signed-in clinician and a
written rationale, are validated against a stored recommendation, and are written to the hash-chained audit log
under that clinician's staff ID (taken from their account, never from the request). Privacy guard: obvious
identifiers (email/phone/SSN) are rejected in free-text fields to protect patient privacy.
Accounts: patients see only their own records; clinicians see the review queue. See src/auth and docs/limitations.md
for what this authentication does and does not provide.
"""
from __future__ import annotations

import json
import math
import re
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional, Union

from fastapi import Depends, FastAPI, HTTPException, Path, Query, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from src.api import pages
from src.api.auth_routes import DEMO_CLINICIAN, DEMO_PATIENT, router as auth_router
from src.api.context import Ctx, ctx, get_ctx, reset_context  # noqa: F401  (re-exported: tests and scripts use main.ctx / main.reset_context)
from src.api.deps import Clinician, require_clinician, require_patient
from src.api.demo import demo_encounters
from src.api.knowledge_routes import router as knowledge_router
from src.auth import AuthError, Session
from src.config import get_settings
from src.contracts.agent_io import ExecutionMode
from src.contracts.recommendation import Recommendation, RecommendationStatus, DraftPrescription
from src.ingest.builders import build_state
from src.orchestrator.runner import RunResult
from src.safety.phi import find_phi
from src.safety.red_flags import EMERGENCY_TEXT

SET = get_settings()
DISCLAIMER = "CareTrace is a Clinical Decision Support System (CDSS). It does NOT replace a licensed clinician or constitute medical advice. In an emergency, call emergency services immediately. Every AI output must be reviewed and approved by a qualified clinician before it is acted upon."


@asynccontextmanager
async def lifespan(_app: FastAPI):
    get_ctx()                                   # open the database / reload persisted records at start-up, not on the first request
    if SET.demo_mode:
        seed_demo_accounts()
    yield


app = FastAPI(title="CareTrace - Agentic AI in Personalised Medicine (CDSS)", version="1.0.0", description=DISCLAIMER, lifespan=lifespan,
              docs_url=None if SET.app_env == "production" else "/docs", redoc_url=None, openapi_url=None if SET.app_env == "production" else "/openapi.json")
app.include_router(auth_router)
app.include_router(knowledge_router)


@app.exception_handler(AuthError)
async def auth_error_handler(_request: Request, exc: AuthError):
    return JSONResponse({"detail": exc.message, "field": exc.field}, status_code=exc.status)


# ----------------------------------------------------------------------------- models
class LabIn(BaseModel):
    value: float
    unit: Optional[str] = None


class PatientSubmission(BaseModel):
    """A check-in from the SIGNED-IN patient. Identity (patient_id) is taken from the session, never from this body;
    date of birth and gender fall back to the account profile when omitted."""
    birth_date: Optional[str] = Field(default=None, description="ISO date YYYY-MM-DD; defaults to the account's date of birth")
    gender: Optional[str] = Field(default=None, max_length=10)
    conditions: List[str] = Field(default_factory=list, max_length=40)
    medications: List[str] = Field(default_factory=list, max_length=60)
    allergies: List[str] = Field(default_factory=list, max_length=40)
    labs: Dict[str, Union[float, LabIn]] = Field(default_factory=dict)
    vitals: Dict[str, Union[float, LabIn]] = Field(default_factory=dict)
    chief_complaint: str = Field(default="", max_length=500)
    symptoms: str = Field(default="", max_length=1000)
    notes: str = Field(default="", max_length=2000)

    @field_validator("birth_date")
    @classmethod
    def _bd(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        try:
            d = datetime.fromisoformat(v)
        except ValueError:
            raise ValueError("birth_date must be an ISO date (YYYY-MM-DD)")
        if not (1900 <= d.year <= datetime.now().year):
            raise ValueError("birth_date out of range")
        return v

    @field_validator("labs", "vitals")
    @classmethod
    def _finite(cls, v):
        for k, x in v.items():
            val = x if isinstance(x, (int, float)) else x.value
            if not math.isfinite(val) or not (0 <= val <= 100000):
                raise ValueError(f"{k}: value out of range")
        return v


class ReviewRequest(BaseModel):
    action: Literal["approve", "modify", "reject", "acknowledge"]
    clinician_id: Optional[str] = Field(default=None, pattern=r"^[A-Za-z0-9][A-Za-z0-9_\-.]{2,40}$",
                                        description="Ignored for signed-in clinicians (their staff ID is used). Required only with the legacy X-API-Token.")
    rationale: str = Field(..., min_length=5, max_length=2000)
    modifications: Optional[str] = Field(default=None, max_length=2000)


class PrescriptionSignRequest(BaseModel):
    """Clinician sign/reject/cancel a single draft prescription."""
    action: Literal["sign", "reject", "cancel"]
    notes: Optional[str] = Field(default=None, max_length=1000, description="Optional clinician note")



class ExtractReportRequest(BaseModel):
    text: str = Field(..., min_length=5, max_length=10000)


def _specs(d: Dict[str, Union[float, LabIn]]) -> Dict[str, Any]:
    return {k: (v if isinstance(v, (int, float)) else ((v.value, v.unit) if v.unit else v.value)) for k, v in d.items()}


def _summary(r: Recommendation) -> Dict[str, Any]:
    return {"recommendation_id": r.recommendation_id, "patient_id": r.patient_id, "state_version": r.state_version, "status": r.status.value,
            "deferred": r.deferred, "mode": r.execution_mode, "n_actions": len(r.clinical_actions), "n_risk_flags": len(r.risk_flags),
            "aggregate_confidence": r.aggregate_confidence, "created_at": r.created_at.isoformat(), "reviewed_by": r.reviewed_by,
            "emergency": bool(r.emergency_flags), "agent_invocations": r.metrics.agent_invocations, "reasoning_invocations": r.metrics.reasoning_invocations}


def _evidence_dict(e) -> Dict[str, Any]:
    return {"id": e.id, "title": e.title, "source": e.source.value, "url": e.url, "publication_date": e.publication_date, "snippet": e.snippet, "verbatim": e.verbatim}


def _patient_view(r: Recommendation) -> Dict[str, Any]:
    """What the signed-in patient sees. Design choice (see docs/limitations.md #16): a flag or action is never shown
    without the reasoning and evidence behind it, so patients get the same 'why', not just the clinician's 'what'.
    Held back deliberately, as internal process rather than clinical information: agent-vs-agent conflicts, the raw
    agent trace, and the CONTENT of anything withheld by validation (the patient is told something was withheld,
    not what, since an unverified claim shown 'for context' is easy to misread as endorsed)."""
    claim_evidence = {c.claim_id: [_evidence_dict(e) for e in c.evidence] for c in r.supported_claims}
    return {
        "recommendation_id": r.recommendation_id, "patient_id": r.patient_id, "state_version": r.state_version, "status": r.status.value,
        "requires_clinician_review": r.requires_clinician_review, "emergency": bool(r.emergency_flags), "emergency_text": EMERGENCY_TEXT if r.emergency_flags else None,
        "red_flags": r.emergency_flags, "deferred": r.deferred, "abstention_reason": r.abstention_reason,
        "items_for_clinician": [{"severity": f.severity.value, "text": f.description, "why": f.mitigation_advice,
                                 "evidence": [_evidence_dict(e) for e in f.evidence]} for f in r.risk_flags],
        "proposed_actions": [f"{a.action_type} {a.target}" for a in r.clinical_actions],
        "action_details": [{"summary": f"{a.action_type} {a.target}", "urgency": a.urgency, "rationale": a.rationale, "options": a.options,
                            "evidence": [ev for cid in a.claim_ids for ev in claim_evidence.get(cid, [])]} for a in r.clinical_actions],
        "what_changed": r.what_changed, "mode": r.execution_mode, "agent_invocations": r.metrics.agent_invocations,
        "reasoning_invocations": r.metrics.reasoning_invocations, "carried_forward": r.metrics.reasoning_invocations == 0 and not r.emergency_flags,
        "aggregate_confidence": r.aggregate_confidence, "validation_passed": bool(r.validation_report and r.validation_report.passed),
        "n_evidence": len({e.id for c in r.supported_claims for e in c.evidence}), "n_flagged_claims": len(r.flagged_claims), "disclaimer": DISCLAIMER}


def _register(res: RunResult) -> Recommendation:
    r = res.recommendation
    ctx.runs[r.patient_id] = res
    if res.state.version is not None and r.execution_mode == ExecutionMode.SELECTIVE_REPLAN.value:
        ctx.by_version[(r.patient_id, res.state.version)] = res
    ctx.recs[r.recommendation_id] = r
    ctx.rec_order.append(r.recommendation_id)
    ctx.save_recommendation(r)
    return r


def _analyse(sub_state, mode: ExecutionMode, texts: List[str]) -> Recommendation:
    with ctx.lock:
        prev = ctx.store.latest(sub_state.patient_id)
        sealed = ctx.store.append_state(sub_state)
        ctx.audit.log_event("state_appended", {"patient_id": sealed.patient_id, "version": sealed.version, "state_hash": sealed.state_hash})
        # After a server restart the per-patient agent cache is empty: prev_run is then None and every agent re-runs (correct, just slower).
        prev_run = ctx.by_version.get((sealed.patient_id, prev.version)) if prev else None
        res = ctx.runner.run(sealed, prev=prev, history=ctx.store.history(sealed.patient_id)[:-1], prev_run=prev_run, mode=mode, texts=texts)
        return _register(res)


def _owned(rec_id: str, sess: Session) -> Recommendation:
    """A patient may only read their own records. Someone else's id looks exactly like a missing one (404)."""
    r = ctx.recs.get(rec_id)
    if r is None or r.patient_id != sess.user.patient_id:
        raise HTTPException(status_code=404, detail="Recommendation not found")
    return r


def seed_demo_accounts() -> None:
    """Demo mode only: one patient (with 3 recorded visits) and one clinician, so a fresh install has something to look at."""
    a = ctx.auth
    with ctx.lock:
        pat = a.find_by_email(DEMO_PATIENT["email"]) or a.register_patient(DEMO_PATIENT["email"], DEMO_PATIENT["password"], "Alex Demo", "1955-03-01", "male", True)
        if not ctx.store.latest(pat.patient_id):
            encs = demo_encounters(pat.patient_id)
            for st in encs:
                _analyse(st, ExecutionMode.SELECTIVE_REPLAN, [])
            last = encs[-1]
            a.save_last_submission(pat.id, {"conditions": [f"{c.display_name} ({c.code})" for c in last.active_conditions], "medications": [" ".join(x for x in (m.name, m.dosage, m.frequency) if x and x != "unspecified") for m in last.active_medications],
                                            "allergies": [x.substance for x in last.allergies], "labs": {k: v.value for k, v in last.latest_labs.items()},
                                            "vitals": {k: v.value for k, v in last.vitals.items()}})
        if not a.find_by_email(DEMO_CLINICIAN["email"]):
            a.register_clinician(DEMO_CLINICIAN["email"], DEMO_CLINICIAN["password"], "Dr. Demo Clinician", "DR-DEMO", "", True, skip_invite=True)


# ----------------------------------------------------------------------------- endpoints
@app.get("/health")
def health() -> Dict[str, Any]:
    return {"status": "ok", "version": app.version}


@app.get("/api/v1/system/status")
def system_status(_: Clinician = Depends(require_clinician)) -> Dict[str, Any]:
    from src.knowledge import get_evidence_store
    from src.llm.client import FallbackLLMClient
    llm_info = {"provider": SET.llm_provider, "connected": ctx.llm is not None, "error": ctx.llm_error, "local_only": SET.llm_local_only, "escalation_enabled": ctx.runner.enable_llm_escalation}
    if isinstance(ctx.llm, FallbackLLMClient):
        llm_info.update({"fallback_provider": SET.llm_fallback_provider, "last_used": ctx.llm.last_used})
    return {"status": "ok", "version": app.version, "agents": len(ctx.runner.agents), "evidence_chunks": len(get_evidence_store().chunks),
            "llm": llm_info, "audit_chain_ok": ctx.audit.verify_chain(), "demo_mode": SET.demo_mode, "persistent": ctx.persist}


@app.post("/api/v1/patient/analyse")
def analyse(sub: PatientSubmission, request: Request, mode: ExecutionMode = Query(ExecutionMode.SELECTIVE_REPLAN), sess: Session = Depends(require_patient)) -> Dict[str, Any]:
    if not ctx.limiter.allow(f"analyse:{sess.user.id}", 40, 3600):
        raise HTTPException(status_code=429, detail="You've sent a lot of check-ins in the last hour. Please wait a little before sending another.")
    phi = find_phi([sub.chief_complaint, sub.symptoms, sub.notes, *sub.conditions, *sub.medications, *sub.allergies])
    if phi:
        raise HTTPException(status_code=422, detail=f"Possible personal identifiers detected in your text ({', '.join(phi)}). To protect your privacy, please do not include email addresses, phone numbers, or national ID numbers in the notes/symptoms fields. Your name and medical data are stored securely via your account — you do not need to type them in the text boxes.")
    try:
        state = build_state(sess.user.patient_id, sub.birth_date or sess.user.birth_date, sub.gender or sess.user.gender or "unknown", timestamp=datetime.now(timezone.utc),
                            conditions=sub.conditions, meds=sub.medications, allergies=sub.allergies, labs=_specs(sub.labs), vitals=_specs(sub.vitals),
                            notes=[n for n in (sub.chief_complaint, sub.symptoms, sub.notes) if n])
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    rec = _analyse(state, mode, [sub.chief_complaint, sub.symptoms, sub.notes])
    ctx.auth.save_last_submission(sess.user.id, {"conditions": sub.conditions, "medications": sub.medications, "allergies": sub.allergies,
                                                 "labs": {k: (v if isinstance(v, (int, float)) else v.value) for k, v in sub.labs.items()},
                                                 "vitals": {k: (v if isinstance(v, (int, float)) else v.value) for k, v in sub.vitals.items()}})
    return _patient_view(rec)


def _patient_history_summary(r: Recommendation) -> Dict[str, Any]:
    return {"recommendation_id": r.recommendation_id, "state_version": r.state_version, "status": r.status.value,
            "created_at": r.created_at.isoformat(), "reviewed": r.reviewed_by is not None, "deferred": r.deferred, "emergency": bool(r.emergency_flags),
            "n_items": len(r.risk_flags), "n_actions": len(r.clinical_actions)}


@app.get("/api/v1/patient/me/recommendations")
def my_recommendations(sess: Session = Depends(require_patient)) -> Dict[str, Any]:
    with ctx.lock:
        items = [_patient_history_summary(ctx.recs[i]) for i in reversed(ctx.rec_order) if ctx.recs[i].patient_id == sess.user.patient_id]
    return {"items": items, "count": len(items)}


@app.get("/api/v1/patient/me/last-submission")
def my_last_submission(sess: Session = Depends(require_patient)) -> Dict[str, Any]:
    return {"submission": ctx.auth.last_submission(sess.user.id)}


@app.post("/api/v1/patient/extract-report")
def extract_report(req: ExtractReportRequest, sess: Session = Depends(require_patient)) -> Dict[str, Any]:
    from src.ingest.report_parser import MedicalReportParser
    parser = MedicalReportParser()
    res = parser.parse(req.text)
    return {
        "labs": {k: {"value": v[0], "unit": v[1]} for k, v in res.labs.items()},
        "vitals": {k: {"value": v[0], "unit": v[1]} for k, v in res.vitals.items()},
        "medications": res.medications,
        "extracted_items": [{"analyte": item.analyte, "value": item.value, "unit": item.unit, "confidence": item.confidence, "raw_text": item.raw_text} for item in res.extracted_items],
        "warnings": res.warnings,
        "report_date": res.report_date,
    }


@app.get("/api/v1/patient/me/timeline")
def my_timeline(sess: Session = Depends(require_patient)) -> Dict[str, Any]:
    """The patient's own recorded values over time (for the trend charts). No hashes, no other people's data."""
    pid = sess.user.patient_id
    latest_by_version: Dict[int, Recommendation] = {}
    for rid in ctx.rec_order:
        r = ctx.recs[rid]
        if r.patient_id == pid and r.state_version is not None:
            latest_by_version[r.state_version] = r
    out = []
    for st in ctx.store.history(pid):
        r = latest_by_version.get(st.version)
        out.append({"version": st.version, "timestamp": st.timestamp.isoformat(),
                    "labs": {k: [v.value, v.unit] for k, v in st.latest_labs.items()}, "vitals": {k: [v.value, v.unit] for k, v in st.vitals.items()},
                    "medications": [m.name for m in st.active_medications], "allergies": [a.substance for a in st.allergies],
                    "recommendation_id": r.recommendation_id if r else None, "status": r.status.value if r else None})
    return {"versions": out}


@app.get("/api/v1/patient/status/{rec_id}")
def patient_status(rec_id: str, sess: Session = Depends(require_patient)) -> Dict[str, Any]:
    """The signed-in patient's own recommendation: what was found and, once a clinician has acted, what they decided.
    The web page polls this (no push channel in the prototype; see docs/limitations.md)."""
    r = _owned(rec_id, sess)
    v = _patient_view(r)
    v["created_at"] = r.created_at.isoformat()
    v["reviewed"] = r.reviewed_by is not None
    v["reviewed_by"] = r.reviewed_by
    v["reviewed_at"] = r.reviewed_at.isoformat() if r.reviewed_at else None
    v["clinician_notes"] = r.review_notes
    return v


@app.get("/api/v1/recommendations")
def list_recs(status: Optional[str] = None, _: Clinician = Depends(require_clinician)) -> Dict[str, Any]:
    with ctx.lock:
        items = [_summary(ctx.recs[i]) for i in reversed(ctx.rec_order)]
    if status:
        items = [i for i in items if i["status"] == status]
    return {"items": items, "count": len(items)}


@app.get("/api/v1/recommendations/{rec_id}")
def get_rec(rec_id: str, _: Clinician = Depends(require_clinician)) -> Dict[str, Any]:
    r = ctx.recs.get(rec_id)
    if r is None:
        raise HTTPException(status_code=404, detail="Recommendation not found")
    return json.loads(r.model_dump_json())


@app.post("/api/v1/recommendations/{rec_id}/review")
def review(rec_id: str, req: ReviewRequest, clin: Clinician = Depends(require_clinician)) -> Dict[str, Any]:
    who = clin.staff_id or req.clinician_id          # signed-in clinicians can't sign as someone else
    if not who:
        raise HTTPException(status_code=422, detail="clinician_id is required when acting with the API token.")
    with ctx.lock:
        r = ctx.recs.get(rec_id)
        if r is None:
            raise HTTPException(status_code=404, detail="Recommendation not found")
        if r.reviewed_by:
            raise HTTPException(status_code=409, detail=f"Already reviewed by {r.reviewed_by}")
        if r.deferred and req.action in ("approve", "modify"):
            raise HTTPException(status_code=409, detail="A deferred/escalated recommendation contains no approved actions; use 'acknowledge' or 'reject'")
        if not r.deferred and req.action == "acknowledge":
            raise HTTPException(status_code=409, detail="'acknowledge' applies only to deferred/escalated recommendations")
        if req.action == "modify" and not (req.modifications and req.modifications.strip()):
            raise HTTPException(status_code=422, detail="'modify' requires a description of the modifications")
        upd: Dict[str, Any] = {"reviewed_by": who, "reviewed_at": datetime.now(timezone.utc),
                               "review_notes": req.rationale + (f" | MODIFICATIONS: {req.modifications}" if req.modifications else "")}
        if req.action == "approve":
            upd.update(status=RecommendationStatus.CLINICIAN_APPROVED, requires_clinician_review=False)
        elif req.action == "modify":
            upd.update(status=RecommendationStatus.CLINICIAN_MODIFIED, requires_clinician_review=False)
        elif req.action == "reject":
            upd.update(status=RecommendationStatus.REJECTED, requires_clinician_review=False)
        else:
            upd.update(status=RecommendationStatus.ESCALATED)
        new = Recommendation.model_validate({**r.model_dump(), **upd})
        ctx.recs[rec_id] = new
        ctx.save_recommendation(new)
        ctx.audit.log_event("clinician_review", {"recommendation_id": rec_id, "patient_id": r.patient_id, "action": req.action, "clinician_id": who,
                                                 "rationale": req.rationale, "modifications": req.modifications, "status": new.status.value})
    return _summary(new)


@app.post("/api/v1/recommendations/{rec_id}/prescriptions/{rx_id}/sign")
def sign_prescription(rec_id: str, rx_id: str, req: PrescriptionSignRequest, clin: Clinician = Depends(require_clinician)) -> Dict[str, Any]:
    """Clinician signs, rejects or cancels a single draft prescription on an already-reviewed recommendation.
    The recommendation must be approved/modified first; you cannot sign prescriptions on pending or deferred cases."""
    with ctx.lock:
        r = ctx.recs.get(rec_id)
        if r is None:
            raise HTTPException(status_code=404, detail="Recommendation not found")
        if r.status not in (RecommendationStatus.CLINICIAN_APPROVED, RecommendationStatus.CLINICIAN_MODIFIED):
            raise HTTPException(status_code=409, detail="Prescriptions can only be signed after the recommendation has been approved by a clinician.")
        rx = next((p for p in r.draft_prescriptions if p.prescription_id == rx_id), None)
        if rx is None:
            raise HTTPException(status_code=404, detail="Draft prescription not found")
        if rx.status not in ("draft",):
            raise HTTPException(status_code=409, detail=f"Prescription is already '{rx.status}'; cannot change it again.")
        who = clin.staff_id
        now = datetime.now(timezone.utc)
        import hashlib, json as _json
        sig_payload = {"prescription_id": rx_id, "recommendation_id": rec_id, "clinician": who, "action": req.action, "signed_at": now.isoformat()}
        digital_sig = hashlib.sha256(_json.dumps(sig_payload, sort_keys=True).encode()).hexdigest()[:32]
        new_status = {"sign": "signed", "reject": "rejected", "cancel": "cancelled"}[req.action]
        updated_rx = rx.model_copy(update={"status": new_status, "signed_by": who, "signed_at": now,
                                           "digital_signature": digital_sig,
                                           "audit_notes": req.notes or f"{req.action.capitalize()} by {who} at {now.isoformat()}"})
        new_prescriptions = [updated_rx if p.prescription_id == rx_id else p for p in r.draft_prescriptions]
        new_r = Recommendation.model_validate({**r.model_dump(), "draft_prescriptions": [p.model_dump() for p in new_prescriptions]})
        ctx.recs[rec_id] = new_r
        ctx.save_recommendation(new_r)
        ctx.audit.log_event("prescription_signed", {
            "recommendation_id": rec_id, "prescription_id": rx_id, "patient_id": r.patient_id,
            "medication": rx.medication, "action": req.action, "clinician_id": who,
            "digital_signature": digital_sig, "notes": req.notes,
        })
    return {"prescription_id": rx_id, "status": new_status, "signed_by": who,
            "signed_at": updated_rx.signed_at.isoformat() if updated_rx.signed_at else None,
            "digital_signature": digital_sig, "medication": rx.medication}


@app.get("/api/v1/patient/me/prescriptions")
def my_prescriptions(sess: Session = Depends(require_patient)) -> Dict[str, Any]:
    """Patient's own signed prescriptions across all their recommendations."""
    pid = sess.user.patient_id
    result = []
    with ctx.lock:
        for rid in ctx.rec_order:
            r = ctx.recs[rid]
            if r.patient_id != pid:
                continue
            for rx in r.draft_prescriptions:
                if rx.status == "signed":
                    result.append({
                        "prescription_id": rx.prescription_id, "recommendation_id": rid,
                        "state_version": r.state_version, "created_at": r.created_at.isoformat(),
                        "medication": rx.medication, "formulation": rx.formulation, "strength": rx.strength,
                        "dosage": rx.dosage, "route": rx.route, "frequency": rx.frequency,
                        "duration": rx.duration, "sig": rx.sig, "signed_by": rx.signed_by,
                        "signed_at": rx.signed_at.isoformat() if rx.signed_at else None,
                        "safety_warnings": rx.safety_warnings, "audit_notes": rx.audit_notes,
                    })
    return {"prescriptions": result, "count": len(result)}


@app.get("/api/v1/patients")
def patients(_: Clinician = Depends(require_clinician)) -> Dict[str, Any]:
    out = []
    for p in ctx.store.patients():
        recs = [ctx.recs[i] for i in ctx.rec_order if ctx.recs[i].patient_id == p]
        out.append({"patient_id": p, "versions": len(ctx.store.history(p)), "awaiting_review": sum(1 for r in recs if not r.reviewed_by),
                    "last_activity": recs[-1].created_at.isoformat() if recs else None, "emergency": any(r.emergency_flags for r in recs)})
    return {"patients": out}


@app.get("/api/v1/patients/{pid}/timeline")
def timeline(pid: str, _: Clinician = Depends(require_clinician)) -> Dict[str, Any]:
    hist = ctx.store.history(pid)
    if not hist:
        raise HTTPException(status_code=404, detail="Unknown patient")
    recs = {}
    for rid in ctx.rec_order:
        r = ctx.recs[rid]
        if r.patient_id == pid:
            recs.setdefault(r.state_version, []).append(_summary(r))
    return {"patient_id": pid, "chain_verified": ctx.store.verify_chain(pid),
            "versions": [{"version": s.version, "state_hash": s.state_hash, "previous_state_hash": s.previous_state_hash, "timestamp": s.timestamp.isoformat(),
                          "labs": {k: [v.value, v.unit] for k, v in s.latest_labs.items()}, "vitals": {k: [v.value, v.unit] for k, v in s.vitals.items()},
                          "medications": [m.name for m in s.active_medications], "allergies": [a.substance for a in s.allergies],
                          "recommendations": recs.get(s.version, [])} for s in hist]}


@app.post("/api/v1/patients/{pid}/rerun")
def rerun(pid: str, mode: ExecutionMode = Query(ExecutionMode.FULL_RERUN), _: Clinician = Depends(require_clinician)) -> Dict[str, Any]:
    """Re-analyse the latest state in another mode (no new version) to compare selective vs full re-run."""
    with ctx.lock:
        hist = ctx.store.history(pid)
        if not hist:
            raise HTTPException(status_code=404, detail="Unknown patient")
        cur, prev = hist[-1], (hist[-2] if len(hist) > 1 else None)
        prev_run = ctx.by_version.get((pid, prev.version)) if prev else None
        res = ctx.runner.run(cur, prev=prev, history=hist[:-1], prev_run=None if mode == ExecutionMode.FULL_RERUN else prev_run, mode=mode)
        rec = _register(res)
    return _summary(rec) | {"what_changed": rec.what_changed}


@app.post("/api/v1/demo/seed")
def seed_demo(patient_id: str = Query("DEMO-T2D-CKD", pattern=r"^[A-Za-z0-9][A-Za-z0-9_\-]{1,40}$"), _: Clinician = Depends(require_clinician)) -> Dict[str, Any]:
    if not SET.demo_mode:
        raise HTTPException(status_code=404, detail="Demo data is switched off on this server.")
    out = []
    with ctx.lock:
        if ctx.store.latest(patient_id):
            raise HTTPException(status_code=409, detail="Demo patient already loaded; use another patient_id")
        for st in demo_encounters(patient_id):
            rec = _analyse(st, ExecutionMode.SELECTIVE_REPLAN, [])
            out.append(_summary(rec))
    return {"patient_id": patient_id, "recommendations": out}


@app.get("/api/v1/eval/results")
def eval_results() -> Dict[str, Any]:
    p = SET.results_dir / "results.json"
    if not p.exists():
        raise HTTPException(status_code=404, detail="No evaluation results yet. Run: python run.py eval")
    return json.loads(p.read_text(encoding="utf-8"))


@app.get("/api/v1/audit/verify")
def audit_verify(_: Clinician = Depends(require_clinician)) -> Dict[str, Any]:
    return {"chain_ok": ctx.audit.verify_chain(), "events": len(ctx.audit.read_all())}


@app.get("/api/v1/audit/prescriptions")
def audit_prescriptions(patient_id: Optional[str] = None, _: Clinician = Depends(require_clinician)) -> Dict[str, Any]:
    """Return all prescription_signed audit events (optionally filtered by patient_id).
    Provides the prescription-specific audit trail required by the workflow."""
    events = [e for e in ctx.audit.read_all() if e.get("event") == "prescription_signed"]
    if patient_id:
        events = [e for e in events if e.get("payload", {}).get("patient_id") == patient_id]
    return {"events": events, "count": len(events), "chain_ok": ctx.audit.verify_chain()}


# ----------------------------------------------------------------------------- website
if SET.frontend_dir.exists():
    if SET.figs_dir.exists():
        app.mount("/assets/figs", StaticFiles(directory=str(SET.figs_dir)), name="figs")
    app.mount("/assets", StaticFiles(directory=str(SET.frontend_dir / "assets")), name="assets")
    pages.install(app)
