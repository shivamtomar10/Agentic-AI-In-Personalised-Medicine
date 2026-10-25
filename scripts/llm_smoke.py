#!/usr/bin/env python3
"""Measure the guarded LLM narration path on real scenarios.

Free ways to get a live model (recommended - no cost):
  1) Ollama, fully local and private (needs ollama serve & ollama pull llama3.1:8b):
       export LLM_PROVIDER=ollama
  2) Google Gemini free tier:
       export LLM_PROVIDER=gemini LLM_LOCAL_ONLY=false GEMINI_API_KEY=... GEMINI_MODEL=gemini-2.5-flash

Then run: python run.py llm-smoke
"""
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from collections import Counter
from eval.harness.scenarios import build_pair, load_adaptation
from src.llm import get_llm_client
from src.orchestrator import PipelineConfig, PipelineRunner

def check_ollama_alive(host: str) -> bool:
    try:
        req = urllib.request.Request(f"{host}/api/version")
        with urllib.request.urlopen(req, timeout=1.5):
            return True
    except Exception:
        return False

llm = get_llm_client()
if llm is None:
    print("[NOTICE] LLM_PROVIDER is 'none' or unconfigured. Core pipeline runs rule-based deterministically. Nothing was measured.")
    sys.exit(0)

if llm.name.startswith("ollama"):
    if not check_ollama_alive(llm.host if hasattr(llm, "host") else "http://127.0.0.1:11434"):
        print("[NOTICE] Ollama service is not running on localhost:11434. Start Ollama with 'ollama serve' to run live LLM smoke test.")
        print("         The deterministic safety rules and clinician approval workflow operate independently of LLM service status.")
        sys.exit(0)

rn = PipelineRunner(PipelineConfig(llm=llm))
tally, examples = Counter(), []
esc_tally = Counter()
esc_proposed = esc_kept = 0
esc_examples = []
for suite in ("core", "holdout"):
    for s in load_adaptation(suite)[0]:
        p, c = build_pair(s, 0)
        rec = rn.run(c, prev=p).recommendation
        text = rec.explanation
        outcome = "rejected_by_guard" if "narration rejected" in text else "model_unavailable" if "narration unavailable" in text else "accepted"
        tally[outcome] += 1
        if outcome != "accepted" and len(examples) < 3:
            examples.append((s["id"], text.splitlines()[-1]))
        esc = rec.llm_escalation or {}
        esc_tally[esc.get("status", "not_triggered")] += 1
        if esc.get("status") == "ok":
            esc_proposed += esc.get("n_proposed", 0)
            esc_kept += esc.get("n_proposed", 0) - esc.get("n_dropped_unsupported", 0)
            if esc.get("considerations") and len(esc_examples) < 3:
                esc_examples.append((s["id"], esc["considerations"][0]))
n = sum(tally.values())
print(f"model: {llm.name}  scenarios: {n}")
print("-- narration (ExplanationAgent re-wording) --")
for k in ("accepted", "rejected_by_guard", "model_unavailable"):
    print(f"  {k:18} {tally[k]:3}  ({tally[k] / n:.0%})")
for e in examples:
    print("  example:", e)
print("\n-- escalation (secondary opinion on hard cases only) --")
for k in ("not_triggered", "ok", "unavailable"):
    print(f"  {k:14} {esc_tally[k]:3}  ({esc_tally[k] / n:.0%})")
if esc_proposed:
    print(f"  considerations proposed: {esc_proposed}  kept after evidence-linking: {esc_kept}  ({esc_kept / esc_proposed:.0%})")
for e in esc_examples:
    print("  example:", e)
