import json
import urllib.error

import pytest

from src.audit import AuditLogger
from src.llm import GeminiClient, AnthropicClient, LLMError, OllamaClient, ScriptedLLMClient, get_llm_client
from src.llm.client import FallbackLLMClient
from src.llm import client as llmc
from src.config import Settings
from src.safety import AbstentionPolicy, aggregate_confidence, detect_red_flags, find_phi
from src.agents import ExplanationAgent
from src.contracts.recommendation import Recommendation
from src.contracts.agent_io import AgentInput
from src.ingest.builders import build_state


def test_audit_chain_detects_tampering(tmp_path):
    a = AuditLogger(str(tmp_path / "a.jsonl"))
    a.log_event("x", {"n": 1}); a.log_event("y", {"n": 2})
    assert a.verify_chain() and AuditLogger(str(tmp_path / "a.jsonl")).verify_chain()
    p = tmp_path / "a.jsonl"; p.write_text(p.read_text().replace('"n": 1', '"n": 9'))
    assert not a.verify_chain()


@pytest.mark.parametrize("text,hit", [("crushing chest pain", True), ("I want to end my life", True), ("throat swelling after the pill", True), ("routine follow up, feeling fine", False)])
def test_red_flags(text, hit):
    assert bool(detect_red_flags([text])) == hit


def test_unstable_vitals_are_red_flags():
    assert detect_red_flags([], build_state("P", vitals={"SBP": 200}))


def test_phi_guard():
    assert find_phi(["email me at a@b.com"]) and find_phi(["SSN 123-45-6789"]) and not find_phi(["eGFR 24 and HbA1c 9.4"])


def test_abstention_policy():
    hi = aggregate_confidence([0.9, 0.9], 10, 10, 0, 0, 0, 5)
    lo = aggregate_confidence([0.3, 0.3], 4, 10, 1, 1, 3, 5)
    p = AbstentionPolicy(0.6)
    assert p.decide(hi) is None and "below" in p.decide(lo) and "abstained" in p.decide(hi, "treatment") and AbstentionPolicy(0.6, enabled=False).decide(lo) is None
    assert lo.aggregate < hi.aggregate


def test_llm_provider_selection_and_privacy():
    assert get_llm_client(Settings(llm_provider="none")) is None
    assert isinstance(get_llm_client(Settings(llm_provider="ollama", llm_fallback_provider="none")), OllamaClient)
    with pytest.raises(LLMError):
        get_llm_client(Settings(llm_provider="gemini", llm_local_only=True))
    with pytest.raises(LLMError):
        get_llm_client(Settings(llm_provider="gemini", llm_local_only=False, gemini_api_key="k", gemini_model=""))
    with pytest.raises(LLMError):
        get_llm_client(Settings(llm_provider="anthropic", llm_local_only=True))
    with pytest.raises(LLMError):
        get_llm_client(Settings(llm_provider="anthropic", llm_local_only=False, anthropic_api_key="", anthropic_model="claude-haiku-4-5-20251001"))
    assert isinstance(get_llm_client(Settings(llm_provider="anthropic", llm_local_only=False, anthropic_api_key="k", anthropic_model="claude-haiku-4-5-20251001")), AnthropicClient)
    with pytest.raises(LLMError):
        ScriptedLLMClient([]).generate("x")


def test_ollama_and_gemini_http_paths_with_mocked_transport(monkeypatch):
    seen = {}
    def fake_post(url, body, timeout=60.0):
        seen["url"], seen["body"] = url, body
        return {"response": "hi"} if "generate" in url and "googleapis" not in url else {"candidates": [{"content": {"parts": [{"text": "hey"}]}}]}
    monkeypatch.setattr(llmc, "_post", fake_post)
    assert OllamaClient("http://h:1", "m", 0.1, 7).generate("p", "s") == "hi" and seen["body"]["options"]["seed"] == 7 and seen["body"]["system"] == "s"
    assert GeminiClient("k", "some-model", 0.2, 3).generate("p") == "hey" and "some-model:generateContent" in seen["url"] and seen["body"]["generationConfig"]["seed"] == 3
    with pytest.raises(LLMError):
        OllamaClient("http://h:1", "m").generate("patient email a@b.com")          # PHI never leaves the process


def test_transport_errors_are_not_swallowed(monkeypatch):
    def boom(*a, **k): raise urllib.error.URLError("down")
    monkeypatch.setattr(llmc.urllib.request, "urlopen", boom)
    with pytest.raises(LLMError):
        OllamaClient("http://h:1", "m").generate("hello")


# ---------------------------------------------------------------- ollama+gemini fallback (LLM_FALLBACK_PROVIDER)
def test_fallback_uses_primary_when_primary_succeeds():
    class Boom(GeminiClient):
        def generate(self, prompt, system=None): raise LLMError("fallback should never be called")
    fb = FallbackLLMClient(ScriptedLLMClient(["primary answer"]), Boom("k", "m"))
    assert fb.generate("p") == "primary answer" and fb.last_used == "scripted"


def test_fallback_switches_to_gemini_when_ollama_unreachable(monkeypatch):
    def fake_post(url, body, timeout=60.0, headers=None):
        return {"candidates": [{"content": {"parts": [{"text": "fallback answer"}]}}]}
    monkeypatch.setattr(llmc, "_post", fake_post)

    def boom(*a, **k): raise urllib.error.URLError("connection refused")
    monkeypatch.setattr(llmc.urllib.request, "urlopen", boom)

    fb = FallbackLLMClient(OllamaClient("http://h:1", "m"), GeminiClient("k", "gemini-3.8-flash"))
    assert fb.generate("p") == "fallback answer" and fb.last_used == "gemini"
    assert fb.name == "ollama(fallback:gemini)"


def test_fallback_raises_combined_error_when_both_fail(monkeypatch):
    def boom(*a, **k): raise urllib.error.URLError("down")
    monkeypatch.setattr(llmc.urllib.request, "urlopen", boom)
    fb = FallbackLLMClient(OllamaClient("http://h:1", "m"), GeminiClient("k", "m"))
    with pytest.raises(LLMError, match="Primary LLM failed.*Fallback LLM failed"):
        fb.generate("p")


def test_get_llm_client_wires_ollama_plus_gemini_fallback():
    s = Settings(llm_provider="ollama", llm_fallback_provider="gemini", llm_local_only=False, gemini_api_key="k", gemini_model="gemini-3.8-flash")
    c = get_llm_client(s)
    assert isinstance(c, FallbackLLMClient) and c.primary.name == "ollama" and c.fallback.name == "gemini"


def test_get_llm_client_fallback_config_mistakes_fail_loudly():
    """Rule 6 (fail loudly, not silently): a typo'd or unsafe LLM_FALLBACK_PROVIDER must raise, not be
    silently ignored (this was a real bug - LLM_FALLBACK_PROVIDER used to do nothing for any value
    other than exactly 'gemini', with no warning, no error, nothing)."""
    with pytest.raises(LLMError):  # unsupported fallback target
        get_llm_client(Settings(llm_provider="ollama", llm_fallback_provider="anthropic"))
    with pytest.raises(LLMError):  # fallback == primary makes no sense
        get_llm_client(Settings(llm_provider="ollama", llm_fallback_provider="ollama"))
    with pytest.raises(LLMError):  # local-only must still block a cloud fallback
        get_llm_client(Settings(llm_provider="ollama", llm_fallback_provider="gemini", llm_local_only=True, gemini_api_key="k", gemini_model="m"))
    assert isinstance(get_llm_client(Settings(llm_provider="ollama", llm_fallback_provider="none")), OllamaClient)


def test_anthropic_http_path_with_mocked_transport(monkeypatch):
    seen = {}
    def fake_post(url, body, timeout=60.0, headers=None):
        seen["url"], seen["body"], seen["headers"] = url, body, headers
        return {"content": [{"type": "text", "text": "hey"}]}
    monkeypatch.setattr(llmc, "_post", fake_post)
    out = AnthropicClient("k", "claude-haiku-4-5-20251001", 0.2, 3).generate("p", "sys")
    assert out == "hey" and seen["url"] == "https://api.anthropic.com/v1/messages"
    assert seen["headers"] == {"x-api-key": "k", "anthropic-version": "2023-06-01"}
    assert seen["body"]["model"] == "claude-haiku-4-5-20251001" and seen["body"]["system"] == "sys"
    with pytest.raises(LLMError):
        AnthropicClient("k", "m").generate("patient email a@b.com")          # PHI never leaves the process


def _draft():
    return Recommendation(recommendation_id="r", patient_id="p", explanation="pending", aggregate_confidence=0.9)


def test_explanation_llm_is_guarded_against_new_facts():
    s = build_state("P")
    bad = ExplanationAgent(llm=ScriptedLLMClient(["Take 500 mg of magic now"])).execute(AgentInput(agent_name="explanation", patient_state=s, context={"draft": _draft()}))
    assert "rejected" in bad.narrative and "DECISION SUPPORT ONLY" in bad.narrative and "magic" not in bad.narrative
    good = ExplanationAgent(llm=ScriptedLLMClient(["Plain wording of the same content."])).execute(AgentInput(agent_name="explanation", patient_state=s, context={"draft": _draft()}))
    assert "Plain wording" in good.narrative and "UNVERIFIED" in good.narrative
    assert "DECISION SUPPORT ONLY" in good.narrative and good.narrative.index("DECISION SUPPORT ONLY") < good.narrative.index("Plain wording")   # reword can never delete the disclaimer/findings
    down = ExplanationAgent(llm=ScriptedLLMClient([])).execute(AgentInput(agent_name="explanation", patient_state=s, context={"draft": _draft()}))
    assert "unavailable" in down.narrative and "DECISION SUPPORT ONLY" in down.narrative      # annotated, not silent
