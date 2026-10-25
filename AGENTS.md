# Project Constitution — Agentic AI for Longitudinal Personalized Medicine

## Non-negotiable rules for any agent working in this repo

1. CLINICAL SAFETY: This is decision SUPPORT. No code path may present a
   recommendation as final without an explicit clinician-approval step.
   Every user-facing output carries a visible "requires clinician review" state.
2. NO REAL PATIENT DATA. Only Synthea-generated synthetic data and the
   PhysioNet MIMIC-IV Demo. Never write code that fetches identifiable data.
3. TYPED CONTRACTS: Every agent input and output is a Pydantic v2 model in
   `src/contracts/`. No agent returns free-form dict or str. If an agent needs a
   new field, add it to the contract first.
4. PROVENANCE IS MANDATORY: Any clinical claim object must carry
   `evidence: list[EvidenceRef]` where EvidenceRef resolves to a retrieved
   source span with a stable id. Claims with empty evidence are flagged
   `unsupported` and must not appear in the approved output.
5. DETERMINISM: All LLM calls go through `src/llm/client.py` with temperature,
   seed and model pinned from config. Every call is logged to the audit store
   with prompt hash, response, latency and token counts.
6. NO SILENT FALLBACKS: If a tool or retrieval fails, raise and surface it.
   Never substitute model memory for a failed retrieval.
7. TESTS FIRST for any evaluation metric. Metrics are the deliverable.
8. Write docstrings that map each module to the dissertation section it
   implements (e.g. "Implements RSI, Part A.4 of the proposal").

---
## Implementation status note (added in audit)
Rules 1-6 above are now enforced in code and covered by tests (see AUDIT_REPORT.md). Rule 6 in particular: online connectors raise `RetrievalUnavailableError` and LLM failures raise `LLMError` - nothing silently falls back to model memory.
