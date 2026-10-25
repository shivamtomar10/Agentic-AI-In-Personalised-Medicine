# Agentic AI in Personalised Medicine - longitudinal, state-aware CDSS prototype

Decision-support **research prototype** (synthetic data only, not medical advice). It implements the review paper's multi-agent architecture with a *temporal personalisation* layer: a versioned patient state, a rule-based DeltaEngine, selective agent re-planning, claim-level provenance, adversarial validation, calibrated abstention, and mandatory clinician sign-off.

**Read `AUDIT_REPORT.md` first** (what was wrong with the scaffold, what was fixed, what is still not built) and `docs/limitations.md`.

## Run it (Windows / macOS / Linux, Python 3.11+)

```bash
python -m venv .venv
.venv\Scripts\activate            # Windows   (macOS/Linux: source .venv/bin/activate)
pip install -e ".[dev]"
python run.py test                # ~200 tests
python run.py verify              # end-to-end check through the real API
python run.py eval                # ~90 s; writes eval/results/results.json, docs/results.md, figures
python run.py calibrate            # refit abstention weights, out-of-sample report
python run.py llm-smoke            # guarded LLM narration test (needs Ollama running; not run in the build)
python run.py serve               # the website: http://127.0.0.1:8000  (API docs at /docs in development)
python run.py create-clinician     # add a clinician account from the command line
python run.py messages             # read messages sent through the contact form
```
`python run.py setup` does the venv + install for you. `make <task>` also works if you have GNU make.

## Try the website
`python run.py serve`, open <http://127.0.0.1:8000>. In development the sign-in page shows two demo accounts (**demo mode**, off by default in production):

| Role | Email | Password |
|---|---|---|
| Patient | demo.patient@example.com | Harbor-Lantern-2026 |
| Clinician | demo.clinician@example.com | Copper-Meadow-2026 |

1. Sign in as the **patient** (Patient tab). The demo patient already has three visits. Encounter 1 runs all reasoning agents; encounter 2 (measurement noise only) reuses earlier reasoning; encounter 3 (renal decline, hyperkalaemia, new penicillin allergy) re-plans, stops metformin, flags the allergy conflict and cites evidence. Open **New check-in** (pre-filled from the last one), change a value slightly and send it. Typing "chest pain" triggers the emergency short-circuit.
2. In a **different browser or a private window**, sign in as the **clinician** (Clinician tab). Open a case: agent trace, detected deltas with rule ids, evidence snippets, withheld items, validation findings and the decision form (a written rationale is required; your staff ID is recorded automatically).
3. Back in the patient window the case updates to show the clinician's decision, without reloading.
4. Create your own accounts at **/register** (patients) and **/register/clinician** (invite code `CLINICIAN-DEMO-2026` in demo mode). Try **Forgot password**: with no mail server, the link is written to `logs/outbox.log` and printed in the console.
5. Public pages: **/library** (dictionary-style search over the medicines table and evidence corpus), **/research** (live results and caveats), **/how-it-works**, **/about**, **/contact**.

Before putting this anywhere other than your own machine, read `docs/website.md` (deployment checklist) and `docs/limitations.md` items 11-12.

## Layout
`src/contracts` typed contracts | `src/state` store + DeltaEngine | `src/agents` 8 agents | `src/orchestrator` LangGraph + runner | `src/provenance` gate/extractor/linker | `src/safety` abstention, red flags, PHI guard | `src/api` FastAPI | `config/` editable rules | `data/knowledge/` curated evidence | `eval/` scenarios, faults, metrics, experiments | `frontend/` website: shared layout, page fragments and vanilla JS modules (no build step; fonts self-hosted); `src/auth` accounts, sessions, password hashing | `docs/` architecture, limitations, results.

## Optional LLM (off by default)
Copy `.env.example` to `.env` and set `LLM_PROVIDER` (`ollama` is local and free; cloud providers need `LLM_LOCAL_ONLY=false`, synthetic data only). The clinician console header shows the LLM status. Then `python run.py llm-smoke` measures it.

## (older note) Optional LLM narration
Off by default (`LLM_PROVIDER=none`). `ollama` (local) or `gemini` (cloud, blocked while `LLM_LOCAL_ONLY=true`) can re-word the explanation; a guard rejects any output that adds numbers. Not exercised against live models here.
