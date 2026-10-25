#!/usr/bin/env python3
"""Cross-platform task runner (works on Windows without `make`).

    python run.py setup     # create .venv and install (first time)
    python run.py test      # run the test-suite
    python run.py eval      # full evaluation -> eval/results/results.json + docs/results.md
    python run.py eval-quick
    python run.py cohort    # generate a synthetic longitudinal cohort into data/synthetic
    python run.py calibrate # refit abstention weights (out-of-sample report)
    python run.py llm-smoke # guarded LLM narration test (needs Ollama running)
    python run.py agents    # per-agent PASS/FAIL report
    python run.py verify    # end-to-end check through the real API (no server needed)
    python run.py serve     # start the website at http://127.0.0.1:8000 (home, library, sign-in, /portal, /clinician)
    python run.py create-clinician  # create a clinician account from the command line (prompts for details)
    python run.py messages  # list messages sent through the contact form
    ollama run command : set OLLAMA_DEBUG=1
    ollama serve 
"""
import os
import subprocess
import sys
import venv
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV = ROOT / "venv" if (ROOT / "venv").exists() else (ROOT / ".venv")
PY = VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def sh(*args):
    print("->", " ".join(map(str, args)))
    return subprocess.call([str(a) for a in args], cwd=ROOT)


def py() -> str:
    return str(PY if PY.exists() else sys.executable)


def create_clinician() -> int:
    """Administrator path: creates a clinician without the public invite code."""
    import getpass
    sys.path.insert(0, str(ROOT))
    # pyrefly: ignore [missing-import]
    from src.auth import AuthError, AuthService, Database
    # pyrefly: ignore [missing-import]
    from src.config import get_settings
    s = get_settings()
    svc = AuthService(Database(s.db_path))
    name, email, staff = input("Full name: ").strip(), input("Email: ").strip(), input("Staff ID (e.g. DR-SMITH): ").strip()
    pw = getpass.getpass("Password (min 10 chars): ")
    if getpass.getpass("Repeat password: ") != pw:
        print("Passwords do not match.")
        return 1
    try:
        u = svc.register_clinician(email, pw, name, staff, "", True, skip_invite=True)
    except AuthError as e:
        print("Could not create account:", e.message)
        return 1
    print(f"Created clinician {u.display_name} ({u.staff_id}). They can sign in at /login (Clinician tab).")
    return 0


def list_messages() -> int:
    sys.path.insert(0, str(ROOT))
    # pyrefly: ignore [missing-import]
    from src.auth import Database
    # pyrefly: ignore [missing-import]
    from src.config import get_settings
    rows = Database(get_settings().db_path).all("SELECT id, created_at, topic, name, email, message FROM contact_messages ORDER BY id DESC")
    for r in rows:
        print(f"MSG-{r['id']:05d} | {r['created_at']} | {r['topic']} | {r['name']} <{r['email']}>\n    {r['message']}\n")
    print(f"{len(rows)} message(s).")
    return 0


def main() -> int:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "help"
    rest = sys.argv[2:]
    if cmd == "setup":
        if not PY.exists():
            venv.create(VENV, with_pip=True)
        return sh(PY, "-m", "pip", "install", "-e", ".[dev]")
    if cmd == "test":
        return sh(py(), "-m", "pytest", "-q", *rest)
    if cmd == "eval":
        return sh(py(), "-m", "eval.harness.run_evaluation", *rest)
    if cmd == "eval-quick":
        return sh(py(), "-m", "eval.harness.run_evaluation", "--quick")
    if cmd == "cohort":
        return sh(py(), "scripts/generate_cohort.py", *rest)
    if cmd == "calibrate":
        return sh(py(), "-m", "eval.calibrate_abstention", *rest)
    if cmd == "llm-smoke":
        return sh(py(), "scripts/llm_smoke.py")
    if cmd == "agents":
        return sh(py(), "scripts/check_agents.py")
    if cmd == "verify":
        return sh(py(), "scripts/verify_e2e.py")
    if cmd == "serve":
        return sh(py(), "-m", "uvicorn", "src.api.main:app", "--host", "127.0.0.1", "--port", "8000", *rest)
    if cmd == "create-clinician":
        return create_clinician()
    if cmd == "messages":
        return list_messages()
    print(__doc__)
    return 0 if cmd == "help" else 2


if __name__ == "__main__":
    sys.exit(main())
