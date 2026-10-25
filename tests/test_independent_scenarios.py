from pathlib import Path
import yaml
import pytest

from src.contracts.agent_io import ExecutionMode
from src.ingest.builders import build_state
from src.orchestrator.runner import PipelineRunner

SCENARIOS_PATH = Path(__file__).resolve().parents[1] / "eval" / "scenarios" / "independent_clinical_scenarios.yaml"

def load_independent_scenarios():
    if not SCENARIOS_PATH.exists():
        return []
    with open(SCENARIOS_PATH, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data.get("scenarios", [])

@pytest.mark.parametrize("scen", load_independent_scenarios(), ids=lambda s: s["id"])
def test_independent_scenario_safety(scen):
    runner = PipelineRunner()

    
    prev_dict = scen["prev"]
    curr_dict = scen["curr"]

    prev_st = build_state(
        patient_id=f"PT-INDEP-{scen['id']}",
        birth_date=prev_dict.get("birth_date", "1960-01-01"),
        gender=prev_dict.get("gender", "male"),
        conditions=prev_dict.get("conditions", []),
        meds=prev_dict.get("meds", []),
        allergies=prev_dict.get("allergies", []),
        labs=prev_dict.get("labs", {}),
        vitals=prev_dict.get("vitals", {}),
        timestamp=prev_dict.get("prev_time", "2026-01-10"),
        version=1
    )

    curr_st = build_state(
        patient_id=f"PT-INDEP-{scen['id']}",
        birth_date=curr_dict.get("birth_date", "1960-01-01"),
        gender=curr_dict.get("gender", "male"),
        conditions=curr_dict.get("conditions", []),
        meds=curr_dict.get("meds", []),
        allergies=curr_dict.get("allergies", []),
        labs=curr_dict.get("labs", {}),
        vitals=curr_dict.get("vitals", {}),
        timestamp=curr_dict.get("curr_time", "2026-06-15"),
        version=2
    )

    res = runner.run(curr_st, prev=prev_st, mode=ExecutionMode.FULL_RERUN)
    rec = res.recommendation

    expect = scen.get("expect", {})

    # Check forbidden actions
    for forbidden in expect.get("forbidden_actions", []):
        act_verb, act_target = forbidden[0], forbidden[1].lower()
        for proposed in rec.clinical_actions:
            if proposed.target.lower() == act_target and proposed.action_type.lower() == act_verb.lower():
                pytest.fail(f"Forbidden action '{act_verb} {act_target}' proposed in scenario {scen['id']}")

    # Check required actions
    for req in expect.get("required_actions", []):
        verbs = req[0] if isinstance(req[0], list) else [req[0]]
        target = req[1].lower()
        found = False
        for proposed in rec.clinical_actions:
            if target in proposed.target.lower() and proposed.action_type.lower() in [v.lower() for v in verbs]:
                found = True
                break
        assert found, f"Required action target '{target}' with verbs {verbs} not found in recommendation for scenario {scen['id']}"

