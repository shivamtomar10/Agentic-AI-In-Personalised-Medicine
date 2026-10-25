"""Unit tests for Hybrid rules + LLM integration (no external network / fake LLM)."""
from __future__ import annotations

import pytest
from src.contracts.agent_io import ExecutionMode
from src.contracts.patient import PatientState
from src.contracts.recommendation import ClinicalAction, RiskFlag, RiskSeverity
from src.ingest.builders import build_state
from src.llm.client import LLMClient, LLMError
from src.orchestrator import PipelineConfig, PipelineRunner


class FakeLLMClient(LLMClient):
    def __init__(self, response_text: str = "", should_raise: bool = False):
        self.response_text = response_text
        self.should_raise = should_raise
        self.calls = []

    def generate(self, prompt: str, system: str | None = None) -> str:
        self.calls.append({"prompt": prompt, "system": system})
        if self.should_raise:
            raise LLMError("Fake LLM connection error")
        return self.response_text


def sample_patient() -> PatientState:
    return build_state(
        patient_id="PAT-HYBRID-TEST",
        birth_date="1960-01-01",
        gender="female",
        timestamp="2026-01-01",
        conditions=["diabetes", "hypertension"],
        meds=["metformin"],
        allergies=["penicillin"],
        labs={"eGFR": 25.0, "HbA1c": 8.5},
        vitals={"Systolic BP": 130.0},
        notes=["Patient reports mild fatigue."],
    )


def sample_emergency_patient() -> PatientState:
    return build_state(
        patient_id="PAT-EMERGENCY-TEST",
        birth_date="1960-01-01",
        gender="male",
        timestamp="2026-01-01",
        conditions=["chest_pain"],
        meds=[],
        allergies=[],
        labs={},
        vitals={"Systolic BP": 210.0},
        notes=["Chest pain radiating to left arm"],
    )


def test_llm_mode_off_same_as_before():
    st = sample_patient()

    runner_default = PipelineRunner(PipelineConfig(llm_mode="off"))
    res_default = runner_default.run(st, mode=ExecutionMode.FULL_RERUN)

    fake_llm = FakeLLMClient(response_text='{"suggestions": []}')
    runner_hybrid_off = PipelineRunner(PipelineConfig(llm_mode="off", llm=fake_llm))
    res_off = runner_hybrid_off.run(st, mode=ExecutionMode.FULL_RERUN)

    # Actions and risk flags must be identical
    assert len(res_default.recommendation.clinical_actions) == len(res_off.recommendation.clinical_actions)
    assert len(res_default.recommendation.risk_flags) == len(res_off.recommendation.risk_flags)
    # Verify no hybrid LLM prompt was sent
    assert not any("PATIENT CLINICAL DATA" in c["prompt"] for c in fake_llm.calls)


def test_llm_cannot_remove_rule_flag():
    st = sample_patient()
    # Metformin eGFR < 30 triggers rule-based discontinuation
    fake_json = '''{
        "suggestions": [
            {
                "action_type": "review",
                "target": "metformin",
                "details": "continue metformin as usual",
                "rationale": "patient needs glycemic control",
                "claim_ids": ["EV-FDA-METFORMIN#3"]
            }
        ]
    }'''
    fake_llm = FakeLLMClient(response_text=fake_json)
    runner = PipelineRunner(PipelineConfig(llm_mode="hybrid", llm=fake_llm))
    res = runner.run(st, mode=ExecutionMode.FULL_RERUN)

    rec = res.recommendation
    # Metformin discontinue rule action MUST remain
    discontinue_actions = [a for a in rec.clinical_actions if a.action_type == "discontinue" and "metformin" in a.target]
    assert len(discontinue_actions) > 0

    # The conflicting LLM suggestion MUST be withheld
    conflicting_withheld = [a for a in rec.flagged_actions if "metformin" in a.target.lower()]
    assert len(conflicting_withheld) > 0


def test_fake_evidence_id_is_withheld():
    st = sample_patient()
    fake_json = '''{
        "suggestions": [
            {
                "action_type": "review",
                "target": "lifestyle",
                "details": "Consider dietary review",
                "rationale": "General health",
                "claim_ids": ["EV-FAKE-NONEXISTENT-999"]
            }
        ]
    }'''
    fake_llm = FakeLLMClient(response_text=fake_json)
    runner = PipelineRunner(PipelineConfig(llm_mode="hybrid", llm=fake_llm))
    res = runner.run(st, mode=ExecutionMode.FULL_RERUN)

    rec = res.recommendation
    # Nonexistent evidence ID item must NOT be in clinical_actions
    assert not any("EV-FAKE-NONEXISTENT-999" in a.claim_ids for a in rec.clinical_actions)

    # It MUST be in flagged_actions (withheld)
    withheld_fake = [a for a in rec.flagged_actions if any("EV-FAKE-NONEXISTENT-999" in str(cid) for cid in a.claim_ids)]
    assert len(withheld_fake) > 0


def test_bad_or_empty_llm_output_falls_back():
    st = sample_patient()
    # Case A: LLM error
    error_llm = FakeLLMClient(should_raise=True)
    runner_err = PipelineRunner(PipelineConfig(llm_mode="hybrid", llm=error_llm))
    res_err = runner_err.run(st, mode=ExecutionMode.FULL_RERUN)
    assert "LLM unavailable, rules-only result" in res_err.recommendation.explanation

    # Case B: Bad JSON output
    bad_json_llm = FakeLLMClient(response_text="Not valid JSON response")
    runner_bad = PipelineRunner(PipelineConfig(llm_mode="hybrid", llm=bad_json_llm))
    res_bad = runner_bad.run(st, mode=ExecutionMode.FULL_RERUN)
    assert "LLM unavailable, rules-only result" in res_bad.recommendation.explanation


def test_no_doses_or_prescriptions_from_llm():
    st = sample_patient()
    fake_json = '''{
        "suggestions": [
            {
                "action_type": "prescribe",
                "target": "glipizide",
                "details": "Prescribe glipizide 5 mg daily",
                "rationale": "For diabetes",
                "claim_ids": ["EV-ADA-2024-INTENSIFY#0"]
            },
            {
                "action_type": "review",
                "target": "insulin",
                "details": "Start 10 units at bedtime",
                "rationale": "High HbA1c",
                "claim_ids": ["EV-ADA-2024-INTENSIFY#0"]
            }
        ]
    }'''
    fake_llm = FakeLLMClient(response_text=fake_json)
    runner = PipelineRunner(PipelineConfig(llm_mode="hybrid", llm=fake_llm))
    res = runner.run(st, mode=ExecutionMode.FULL_RERUN)

    rec = res.recommendation
    # Neither prescription nor dose item should be approved in clinical_actions
    assert not any(a.action_type == "prescribe" for a in rec.clinical_actions)
    assert not any("5 mg" in a.details or "10 units" in a.details for a in rec.clinical_actions)

    # Both must be withheld
    assert len(rec.flagged_actions) >= 2


def test_no_llm_call_on_emergency_cases():
    st = sample_emergency_patient()
    fake_llm = FakeLLMClient(response_text='{"suggestions": []}')
    runner = PipelineRunner(PipelineConfig(llm_mode="hybrid", llm=fake_llm))
    res = runner.run(st, mode=ExecutionMode.FULL_RERUN)

    # LLM should never have been called
    assert len(fake_llm.calls) == 0
    assert res.recommendation.deferred is True
    assert "Emergency" in res.recommendation.abstention_reason
