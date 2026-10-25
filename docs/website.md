# The website

A multi-page site served by the same FastAPI app as the API. No build step: HTML fragments + a shared layout, vanilla ES modules, self-hosted fonts (Newsreader for headings, Atkinson Hyperlegible Next for text).

## Pages

| Path | Who | What |
|---|---|---|
| `/` | everyone | Home, with an interactive three-visit demo built from the project's own demo patient |
| `/how-it-works`, `/about`, `/research` | everyone | Explanation, scope and limits, live evaluation results (`/api/v1/eval/results`) |
| `/library` | everyone | Dictionary-style search over the drug table and evidence corpus (autocomplete + results) |
| `/contact`, `/privacy`, `/terms`, `/accessibility` | everyone | Contact form (stored, PHI-guarded, honeypot, rate-limited) and legal/help pages |
| `/login`, `/register`, `/register/clinician`, `/forgot-password`, `/reset-password` | signed-out | Redirect to your dashboard if already signed in |
| `/portal` | patients | Overview and trends, pre-filled check-in, case list and detail (polls for the decision), account |
| `/clinician` | clinicians | Review queue with filters, evidence-linked case detail, decision form, patient records, account |

The two dashboards are guarded on the server: a signed-out browser is redirected and never receives the page. Editing a page: change the file in `frontend/pages/`; the first line (`<!--meta ... -->`) sets title, description, script and nav highlight. The header, footer and safety banner live only in `frontend/layout.html`.

## Security model (what is enforced on the server)

* Passwords: scrypt with a random salt; policy in `src/auth/security.py`.
* Sessions: random token in an HttpOnly, SameSite=Lax cookie (`Secure` in production); only its SHA-256 is stored. Clinician sessions expire after 30 minutes idle / 12 hours absolute, patients 12 hours / 7 days.
* CSRF: every signed-in write needs the `X-CSRF-Token` returned at sign-in; cross-origin `Origin` headers are rejected.
* Lockout: 5 failed sign-ins lock the email for 10 minutes (unknown emails too). Rate limits on sign-in, registration, reset, contact, search.
* Identity: a patient's records are keyed by a random pseudonymous `PT-...` id from their account (never from the request body); a clinician's decision is attributed to the staff ID on their account.
* Headers: CSP (`script-src 'self'`), `X-Frame-Options: DENY`, `nosniff`, referrer and permissions policies, HSTS when `COOKIE_SECURE`.

## Running it for real (checklist)

1. `APP_ENV=production`, `PUBLIC_URL=https://your.domain`, a long random `CLINICIAN_INVITE_CODE` (or blank and use `python run.py create-clinician`). Leave `DEMO_MODE` unset.
2. Terminate HTTPS at a reverse proxy; set `TRUST_PROXY=true` only if the proxy sets `X-Forwarded-For` and is the only way in.
3. Run **one** worker: `python -m uvicorn src.api.main:app --host 127.0.0.1 --port 8000`.
4. Replace `OutboxMailer.send` (`src/auth/mailer.py`) with real email if you want working password resets.
5. Back up `data/` and `logs/`. Have a lawyer read the privacy and terms pages. This remains a synthetic-data research prototype (`docs/limitations.md`).

## Browser test

`pip install playwright && python -m playwright install chromium`, start `python run.py serve` on a **fresh** `data/`, then `python scripts/browser_e2e.py`. It registers a patient, submits a check-in, signs a clinician in in a separate browser context, approves it, and confirms the patient sees the decision.
