#!/usr/bin/env python3
"""End-to-end check through the REAL API (in-process). Replaces the scaffold's hand-built 'verify' script."""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from src.api import main  # noqa: E402
from tests.helpers import Actor  # noqa: E402

main.reset_context(str(Path(tempfile.mkdtemp()) / "audit.jsonl"), invite_code="INVITE-E2E")
c = Actor()                                            # a signed-in clinician (real account, session cookie, CSRF token)
assert c.register_clinician(email="e2e@example.com", staff="DR-E2E", invite="INVITE-E2E").status_code == 201
fails = []


def check(name, cond):
    print(("[OK]   " if cond else "[FAIL] ") + name)
    if not cond:
        fails.append(name)


check("health", c.get("/health").json()["status"] == "ok")
check("signed-out browsers cannot read the queue", __import__("fastapi.testclient", fromlist=["x"]).TestClient(main.app).get("/api/v1/recommendations").status_code == 401)
d = c.post("/api/v1/demo/seed?patient_id=E2E-1").json()["recommendations"]
check("encounter 1 runs 4 reasoning agents", d[0]["reasoning_invocations"] == 4)
check("encounter 2 (noise) reuses reasoning: 0 reasoning agents", d[1]["reasoning_invocations"] == 0)
check("encounter 3 (deterioration) re-plans", d[2]["reasoning_invocations"] >= 3)
rec = c.get(f"/api/v1/recommendations/{d[2]['recommendation_id']}").json()
check("metformin stopped at eGFR 24", any(a["action_type"] == "discontinue" and a["target"] == "metformin" for a in rec["clinical_actions"]))
check("every approved action cites evidence", all(a["claim_ids"] for a in rec["clinical_actions"]))
check("validation passed", rec["validation_report"]["passed"])
check("unknown recommendation cannot be approved", c.post("/api/v1/recommendations/NOPE/review", json={"action": "approve", "rationale": "reviewed ok"}).status_code == 404)
ok = c.post(f"/api/v1/recommendations/{rec['recommendation_id']}/review", json={"action": "approve", "rationale": "reviewed"}).json()
check("approval recorded", ok["status"] == "clinician_approved")
check("audit chain intact", c.get("/api/v1/audit/verify").json()["chain_ok"])
print("\nALL CHECKS PASSED" if not fails else f"\n{len(fails)} CHECK(S) FAILED")
sys.exit(1 if fails else 0)
