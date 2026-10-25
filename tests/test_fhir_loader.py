import pytest

from src.ingest.fhir_loader import bundle_to_states
from src.orchestrator import PipelineRunner


def obs(code, val, unit, when):
    return {"resource": {"resourceType": "Observation", "code": {"coding": [{"code": code}]}, "valueQuantity": {"value": val, "unit": unit}, "effectiveDateTime": when}}


BUNDLE = {"resourceType": "Bundle", "entry": [
    {"resource": {"resourceType": "Patient", "id": "abc", "birthDate": "1955-03-01", "gender": "male"}},
    {"resource": {"resourceType": "Condition", "code": {"coding": [{"code": "44054006", "display": "Type 2 diabetes mellitus"}]}, "onsetDateTime": "2015-01-01T00:00:00Z"}},
    {"resource": {"resourceType": "Condition", "code": {"coding": [{"code": "38341003", "display": "Hypertension"}]}, "onsetDateTime": "2015-01-01T00:00:00Z", "abatementDateTime": "2024-09-01T00:00:00Z"}},
    {"resource": {"resourceType": "MedicationRequest", "status": "active", "authoredOn": "2020-01-01T00:00:00Z", "medicationCodeableConcept": {"coding": [{"display": "Metformin"}]}, "dosageInstruction": [{"text": "1000 mg BID"}]}},
    {"resource": {"resourceType": "MedicationRequest", "status": "active", "authoredOn": "2024-08-01T00:00:00Z", "medicationCodeableConcept": {"text": "Amoxicillin"}, "dosageInstruction": [{"text": "500 mg TID"}]}},
    {"resource": {"resourceType": "AllergyIntolerance", "recordedDate": "2024-08-15T00:00:00Z", "code": {"coding": [{"display": "Penicillin"}]}}},
    obs("33914-3", 72, "mL/min/1.73m2", "2024-01-10T09:00:00Z"), obs("4548-4", 6.9, "%", "2024-01-10T09:00:00Z"),
    obs("33914-3", 24, "mL/min/1.73m2", "2024-10-10T09:00:00Z"), obs("2160-0", 230, "umol/L", "2024-10-10T09:00:00Z"),
    {"resource": {"resourceType": "Observation", "code": {"coding": [{"code": "85354-9"}]}, "effectiveDateTime": "2024-10-10T09:00:00Z",
                  "component": [{"code": {"coding": [{"code": "8480-6"}]}, "valueQuantity": {"value": 148, "unit": "mm[Hg]"}}]}},
]}


def test_bundle_becomes_longitudinal_states():
    st = bundle_to_states(BUNDLE)
    assert [s.version for s in st] == [1, 2] and st[0].patient_id == "FHIR-abc"
    assert st[0].latest_labs["eGFR"].value == 72 and st[1].latest_labs["eGFR"].value == 24 and st[1].latest_labs["Creatinine"].unit == "umol/L"
    assert [m.name for m in st[0].active_medications] == ["Metformin"] and len(st[1].active_medications) == 2
    assert st[0].allergies == [] and st[1].allergies[0].substance == "Penicillin"
    assert {c.display_name: c.clinical_status for c in st[1].active_conditions}["Hypertension"] == "resolved"
    assert {c.display_name: c.clinical_status for c in st[0].active_conditions}["Hypertension"] == "active"
    assert st[1].vitals["Systolic BP"].value == 148


def test_fhir_states_drive_the_real_pipeline():
    a, b = bundle_to_states(BUNDLE)
    rn = PipelineRunner()
    r1 = rn.run(a)
    r2 = rn.run(b, prev=a, prev_run=r1).recommendation
    acts = {(x.action_type, x.target) for x in r2.clinical_actions}
    assert ("discontinue", "metformin") in acts and ("discontinue", "amoxicillin") in acts and r2.validation_report.passed


def test_bad_bundles_fail_loudly():
    with pytest.raises(ValueError):
        bundle_to_states({"entry": []})
    with pytest.raises(ValueError):
        bundle_to_states({"entry": [{"resource": {"resourceType": "Patient", "id": "x"}}]})
