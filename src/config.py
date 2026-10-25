"""Central, pinned configuration (AGENTS.md Rule 5). Implements Part B config/determinism."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]

try:  # optional
    from dotenv import load_dotenv
    # Tests must be hermetic: they must pass/fail the same way regardless of what a developer's local
    # .env happens to contain (this was a real bug - LLM_FALLBACK_PROVIDER=gemini in a shipped .env
    # silently changed get_llm_client()'s behaviour for every bare Settings(llm_provider="ollama") in
    # the test suite, since Settings' field defaults read os.environ at construction time). Skip loading
    # .env under pytest; tests that need specific env values set them explicitly via Settings(...) or
    # monkeypatch.setenv, never implicitly via the repo's own .env file.
    if "PYTEST_CURRENT_TEST" not in os.environ and "PYTEST_VERSION" not in os.environ:
        load_dotenv(PROJECT_ROOT / ".env")
except Exception:  # pragma: no cover
    pass


def _f(name: str, default: float) -> float:
    return float(os.getenv(name) or default)


def _i(name: str, default: int) -> int:
    return int(os.getenv(name) or default)


def _app_env() -> str:
    return (os.getenv("APP_ENV") or "development").strip().lower()


def _flag(name: str, default: bool) -> bool:
    v = os.getenv(name)
    return default if v is None or v == "" else v.strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Settings:
    seed: int = field(default_factory=lambda: _i("SEED", 42))
    replan_threshold: float = field(default_factory=lambda: _f("SELECTIVE_REPLAN_THRESHOLD", 0.3))
    abstention_threshold: float = field(default_factory=lambda: _f("ABSTENTION_THRESHOLD", 0.6))
    llm_escalation_margin: float = field(default_factory=lambda: _f("LLM_ESCALATION_MARGIN", 0.15))
    enable_llm_escalation: bool = field(default_factory=lambda: os.getenv("ENABLE_LLM_ESCALATION", "true").lower() == "true")
    evidence_horizon_years: float = field(default_factory=lambda: _f("EVIDENCE_HORIZON_YEARS", 10))
    max_agent_invocations: int = field(default_factory=lambda: _i("MAX_AGENT_INVOCATIONS", 24))
    run_timeout_s: float = field(default_factory=lambda: _f("RUN_TIMEOUT_SECONDS", 30))
    clinician_api_token: str = field(default_factory=lambda: os.getenv("CLINICIAN_API_TOKEN", ""))
    llm_mode: str = field(default_factory=lambda: os.getenv("LLM_MODE", "off").lower())
    llm_provider: str = field(default_factory=lambda: os.getenv("LLM_PROVIDER", "none").lower())
    llm_fallback_provider: str = field( default_factory=lambda: os.getenv( "LLM_FALLBACK_PROVIDER", "none", ).lower() )
    llm_local_only: bool = field( default_factory=lambda: os.getenv( "LLM_LOCAL_ONLY", "true", ).lower() == "true" )
    llm_temperature: float = field(default_factory=lambda: _f("LLM_TEMPERATURE", 0.1))
    llm_seed: int = field(default_factory=lambda: _i("LLM_SEED", 42))
    ollama_host: str = field(default_factory=lambda: os.getenv("OLLAMA_HOST", "http://127.0.0.1:11434"))
    ollama_model: str = field(default_factory=lambda: os.getenv("OLLAMA_MODEL", "llama3.1:8b"))
    gemini_api_key: str = field(default_factory=lambda: os.getenv("GEMINI_API_KEY", ""))
    gemini_model: str = field(default_factory=lambda: os.getenv("GEMINI_MODEL", ""))
    anthropic_api_key: str = field(default_factory=lambda: os.getenv("ANTHROPIC_API_KEY", ""))
    anthropic_model: str = field(default_factory=lambda: os.getenv("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001"))
    # ---- website / accounts (see src/auth, src/api/pages.py)
    app_env: str = field(default_factory=_app_env)
    site_name: str = field(default_factory=lambda: os.getenv("SITE_NAME") or "CareTrace")
    public_url: str = field(default_factory=lambda: (os.getenv("PUBLIC_URL") or "").rstrip("/"))
    # Demo mode seeds two demo accounts and shows their credentials on the sign-in page. ON by default in
    # development, OFF by default in production. Never leave it on for a server people can reach.
    demo_mode: bool = field(default_factory=lambda: _flag("DEMO_MODE", _app_env() != "production"))
    cookie_secure: bool = field(default_factory=lambda: _flag("COOKIE_SECURE", _app_env() == "production"))
    trust_proxy: bool = field(default_factory=lambda: _flag("TRUST_PROXY", False))
    persist_data: bool = field(default_factory=lambda: _flag("PERSIST_DATA", True))
    # Clinician self-registration needs this invite code. Empty => disabled (use `python run.py create-clinician`).
    clinician_invite_code: str = field(default_factory=lambda: os.getenv("CLINICIAN_INVITE_CODE") or ("CLINICIAN-DEMO-2026" if _flag("DEMO_MODE", _app_env() != "production") else ""))
    db_path: Path = field(default_factory=lambda: Path(os.getenv("DATABASE_PATH") or (PROJECT_ROOT / "data" / "app.db")))
    state_path: Path = field(default_factory=lambda: Path(os.getenv("STATE_PATH") or (PROJECT_ROOT / "data" / "patient_states.jsonl")))
    knowledge_dir: Path = PROJECT_ROOT / "data" / "knowledge"
    config_dir: Path = PROJECT_ROOT / "config"
    logs_dir: Path = PROJECT_ROOT / "logs"
    results_dir: Path = PROJECT_ROOT / "eval" / "results"
    frontend_dir: Path = PROJECT_ROOT / "frontend"
    figs_dir: Path = PROJECT_ROOT / "eval" / "results" / "figs"

    def validate_security_config(self) -> List[str]:
        """Validates configuration against production security rules."""
        warnings: List[str] = []
        if self.app_env == "production":
            if self.demo_mode:
                warnings.append("SECURITY CRITICAL: DEMO_MODE is active in production environment.")
            if self.clinician_invite_code and ("DEMO" in self.clinician_invite_code.upper() or self.clinician_invite_code == "CLINICIAN-DEMO-2026"):
                warnings.append("SECURITY CRITICAL: Demo clinician invite code present in production.")
            if not self.cookie_secure:
                warnings.append("SECURITY WARNING: COOKIE_SECURE is set to false in production.")
            if not self.clinician_api_token:
                warnings.append("SECURITY NOTICE: CLINICIAN_API_TOKEN is unconfigured for API endpoints.")
        return warnings


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()

