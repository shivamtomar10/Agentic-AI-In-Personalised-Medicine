"""Shared application state (patients, recommendations, accounts) behind a lazily-created singleton.

`ctx` is a proxy: the real Ctx is created on first use (or by `reset_context` in tests), so importing the API
module never touches the disk. When `persist=True` (the normal server) three things survive a restart:
accounts and recommendations (SQLite), hash-chained patient states (JSONL) and the audit log (JSONL).
What does NOT survive: the per-patient agent-output cache used by selective re-planning, so the first analysis
after a restart re-runs every agent for that patient (slower, never wrong). See docs/limitations.md.
"""
from __future__ import annotations

import threading
from typing import Dict, List, Optional

from src.audit.logger import AuditLogger
from src.auth import AuthService, Database, OutboxMailer
from src.config import get_settings
from src.contracts.recommendation import Recommendation
from src.llm.client import LLMError, get_llm_client
from src.orchestrator.runner import PipelineConfig, PipelineRunner, RunResult
from src.state.store import PatientStateStore

from src.api.ratelimit import RateLimiter


class Ctx:
    def __init__(self, audit_path: Optional[str] = None, *, db_path=None, state_path=None, persist: bool = False, invite_code: Optional[str] = None):
        SET = get_settings()
        self.persist = persist
        self.lock = threading.RLock()
        self.audit = AuditLogger(audit_path)
        self.db = Database(db_path if (persist and db_path) else ":memory:")
        self.mailer = OutboxMailer(SET.logs_dir / "outbox.log" if persist else None)
        self.auth = AuthService(self.db, self.mailer, invite_code=SET.clinician_invite_code if invite_code is None else invite_code)
        self.limiter = RateLimiter()
        self.store = PatientStateStore(persist_path=str(state_path) if (persist and state_path) else None)
        try:                                   # provider comes from LLM_PROVIDER; 'none' -> no client. Misconfiguration is REPORTED, not hidden.
            self.llm, self.llm_error = get_llm_client(), None
        except LLMError as e:
            self.llm, self.llm_error = None, str(e)
        self.runner = PipelineRunner(PipelineConfig(llm=self.llm), audit=self.audit)
        self.runs: Dict[str, RunResult] = {}
        self.by_version: Dict[tuple, RunResult] = {}
        self.recs: Dict[str, Recommendation] = {}
        self.rec_order: List[str] = []
        if persist:
            self._load_recommendations()

    # ---- persistence of recommendations
    def _load_recommendations(self) -> None:
        for row in self.db.all("SELECT id, doc FROM recommendations ORDER BY rowid"):
            rec = Recommendation.model_validate_json(row["doc"])
            self.recs[rec.recommendation_id] = rec
            self.rec_order.append(rec.recommendation_id)

    def save_recommendation(self, rec: Recommendation) -> None:
        self.db.execute("INSERT INTO recommendations (id, patient_id, state_version, created_at, doc) VALUES (?,?,?,?,?) "
                        "ON CONFLICT(id) DO UPDATE SET doc=excluded.doc",
                        (rec.recommendation_id, rec.patient_id, rec.state_version, rec.created_at.isoformat(), rec.model_dump_json()))

    @classmethod
    def default(cls) -> "Ctx":
        s = get_settings()
        return cls(db_path=s.db_path, state_path=s.state_path, persist=s.persist_data)


_ctx: Optional[Ctx] = None
_init_lock = threading.Lock()


def get_ctx() -> Ctx:
    global _ctx
    if _ctx is None:
        with _init_lock:
            if _ctx is None:
                _ctx = Ctx.default()
    return _ctx


def reset_context(audit_path: Optional[str] = None, **kw) -> None:
    """Fresh in-memory state (used by tests and scripts). Pass db_path/state_path/persist to test persistence."""
    global _ctx
    _ctx = Ctx(audit_path, **kw)


class _CtxProxy:
    def __getattr__(self, name):
        return getattr(get_ctx(), name)


ctx = _CtxProxy()
