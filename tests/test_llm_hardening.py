"""Regression tests for problems found while auditing the LLM layer (they only exercised easy cases before)."""
import pytest
from fastapi.testclient import TestClient

from src.api import context, main
from tests.helpers import Actor
from src.ingest.builders import build_state
from src.llm import LLMError, ScriptedLLMClient
from src.orchestrator import PipelineConfig, PipelineRunner
from src.provenance import ClaimExtractor, EvidenceLinker
from eval.metrics import recommendation_signature as sig

ALLERGIC = dict(conditions=["Type 2 diabetes (E11.9)"], meds=["Metformin 500 mg BID", "Cefuroxime 500 mg BID"], allergies=["Penicillin (anaphylaxis)"], labs={"eGFR": 70, "HbA1c": 6.8}, vitals={"SBP": 120})
TRUE = "Penicillins should be avoided in patients with a documented penicillin allergy pending allergy evaluation"
WRONG = ["Amoxicillin is completely safe in every penicillin-allergic patient",
         "Cross-reactivity between penicillins and cephalosporins is high overall",                    # false: known lexical false-positive
         "Patients with penicillin allergy should routinely receive penicillin desensitisation"]        # fabricated, potentially harmful


def test_extractor_splits_lines_without_periods_and_strips_bullets():
    cs = ClaimExtractor().extract("- Alpha is true\n* Beta is true\n1. Gamma is true", "x")
    assert [c.assertion for c in cs] == ["Alpha is true", "Beta is true", "Gamma is true"]


def test_strict_linker_blocks_plausible_but_wrong_llm_statements():
    from src.knowledge import get_evidence_store
    snippet = get_evidence_store().get("EV-JACI-2022-PENICILLIN#0").ref.snippet
    strict, loose = EvidenceLinker(strict=True), EvidenceLinker()
    assert strict.entails(TRUE, snippet)
    assert loose.entails(WRONG[2], snippet) and not strict.entails(WRONG[2], snippet)        # documents the leak that strict mode closes
    assert not strict.entails(WRONG[1], get_evidence_store().get("EV-JACI-2022-PENICILLIN#1").ref.snippet)   # 'high' vs 'low'


def test_escalation_never_shows_wrong_statements_and_never_changes_decisions():
    st = build_state("ESC-1", "1960-01-01", "female", **ALLERGIC)
    base = PipelineRunner(PipelineConfig(llm=None, llm_escalation_margin=0.5)).run(st).recommendation
    llm = ScriptedLLMClient(["\n".join([TRUE + ".", *[w + "." for w in WRONG]]), "reworded."])
    r = PipelineRunner(PipelineConfig(llm=llm, llm_escalation_margin=0.5)).run(st).recommendation
    e = r.llm_escalation
    assert e["status"] == "ok" and e["n_proposed"] == 4 and e["considerations"] == [TRUE] and e["n_dropped_unsupported"] == 3
    assert sig(r) == sig(base) and r.aggregate_confidence == base.aggregate_confidence and r.calibrated_confidence == base.calibrated_confidence and r.deferred == base.deferred


def test_escalation_still_works_when_model_omits_trailing_periods():
    st = build_state("ESC-2", "1960-01-01", "female", **ALLERGIC)
    llm = ScriptedLLMClient([TRUE + "\n" + WRONG[2], "reworded."])
    e = PipelineRunner(PipelineConfig(llm=llm, llm_escalation_margin=0.5)).run(st).recommendation.llm_escalation
    assert e["n_proposed"] == 2 and e["considerations"] == [TRUE]


@pytest.fixture()
def api(tmp_path, monkeypatch):
    def make(client_or_exc):
        def fake():
            if isinstance(client_or_exc, Exception):
                raise client_or_exc
            return client_or_exc
        monkeypatch.setattr(context, "get_llm_client", fake)
        main.reset_context(str(tmp_path / "audit.jsonl"), invite_code="INVITE-1")
        pat, doc = Actor(), Actor()
        pat.register_patient()
        doc.register_clinician()
        return pat, doc
    return make


def test_api_actually_uses_the_configured_llm_and_reports_it(api):
    llm = ScriptedLLMClient(["reworded summary."] * 5)
    pat, doc = api(llm)
    h = doc.get("/api/v1/system/status").json()["llm"]
    assert h["connected"] is True and h["error"] is None and main.ctx.runner.cfg.llm is llm and main.ctx.runner.agents["explanation"].llm is llm
    rid = pat.post("/api/v1/patient/analyse", json={"labs": {"egfr": 70}}).json()["recommendation_id"]
    assert "UNVERIFIED" in doc.get(f"/api/v1/recommendations/{rid}").json()["explanation"]     # narration really ran through the API
    assert llm.prompts


def test_api_reports_llm_misconfiguration_instead_of_hiding_it(api):
    pat, doc = api(LLMError("LLM_LOCAL_ONLY=true forbids the cloud provider 'gemini'"))
    h = doc.get("/api/v1/system/status").json()["llm"]
    assert h["connected"] is False and "forbids" in h["error"]
    assert pat.post("/api/v1/patient/analyse", json={"labs": {"egfr": 70}}).status_code == 200   # still serves rule-based results
