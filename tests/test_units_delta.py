import pytest

from src.contracts.delta import DeltaCategory as C
from src.state import DeltaEngine
from src.units import UnitError, to_canonical
from src.ingest.builders import build_state

E = DeltaEngine()


def S(v, when="2026-01-01", **kw):
    return build_state("P", kw.pop("dob", "1960-01-01"), "male", timestamp=when, version=v, **kw)


@pytest.mark.parametrize("an,val,unit,expected", [("creatinine", 176.8, "umol/L", 2.0), ("creatinine", 2.0, "mg/dL", 2.0), ("hba1c", 53, "mmol/mol", 7.0), ("hba1c", 7.0, "%", 7.0),
                                                  ("glucose", 5.5, "mmol/L", 99.09), ("potassium", 5.0, "mEq/L", 5.0), ("hemoglobin", 130, "g/L", 13.0)])
def test_unit_conversion(an, val, unit, expected):
    assert to_canonical(an, val, unit) == pytest.approx(expected, abs=0.05)


def test_unknown_unit_raises():
    with pytest.raises(UnitError):
        to_canonical("creatinine", 1.0, "furlongs")


@pytest.mark.parametrize("a,b,cat,significant", [
    (72, 24, C.ORGAN_FUNCTION_CHANGE, True), (31, 29.6, C.ORGAN_FUNCTION_CHANGE, True),      # unsafe crossing inside noise: NOT suppressed
    (29.6, 30.4, C.NOISE, False), (45.4, 44.6, C.ORGAN_FUNCTION_CHANGE, True), (44.6, 45.4, C.NOISE, False),
    (58, 57, C.NOISE, False), (60, 60, None, False), (90, 78, C.ORGAN_FUNCTION_CHANGE, True)])
def test_egfr_rules(a, b, cat, significant):
    r = E.compute(S(1, labs={"eGFR": a}), S(2, labs={"eGFR": b}))
    if cat is None:
        assert not r.deltas
        return
    assert r.deltas[0].category == cat and r.is_clinically_significant == significant


@pytest.mark.parametrize("an,a,b,sig", [("Potassium", 4.4, 5.8, True), ("Potassium", 5.4, 5.6, True), ("Potassium", 5.6, 5.4, False), ("Potassium", 4.4, 4.5, False),
                                        ("HbA1c", 6.9, 9.4, True), ("HbA1c", 6.9, 7.1, True), ("HbA1c", 7.1, 6.9, False), ("HbA1c", 7.5, 7.6, False),
                                        ("ALT", 30, 150, True), ("ALT", 30, 33, False), ("ALT", 150, 120, False)])
def test_lab_rules(an, a, b, sig):
    assert E.compute(S(1, labs={an: a}), S(2, labs={an: b})).is_clinically_significant == sig


def test_unit_representation_is_not_a_delta():
    r = E.compute(S(1, labs={"Creatinine": (1.5, "mg/dL"), "HbA1c": (7.0, "%")}), S(2, labs={"Creatinine": (132.6, "umol/L"), "HbA1c": (53, "mmol/mol")}))
    assert not r.is_clinically_significant, [d.rationale for d in r.deltas]


def test_med_allergy_condition_deltas():
    base = dict(conditions=["Hypertension (I10)"], meds=["Metformin 500 mg BID"])
    r = E.compute(S(1, **base), S(2, conditions=["Hypertension (I10)", "Pregnancy (Z33.1)"], meds=["Metformin 500 mg BID", "Warfarin 5 mg QD"], allergies=["Penicillin"]))
    cats = {d.category: d.significance for d in r.deltas}
    assert cats[C.NEW_DIAGNOSIS] == 1.0 and cats[C.MEDICATION_ADDED] == 0.9 and cats[C.NEW_ALLERGY] == 1.0
    assert {"treatment", "risk_assessment", "patient_data"} <= E.affected_agents(r)


def test_dose_change_and_reorder():
    r = E.compute(S(1, meds=["Metformin 500 mg BID", "Lisinopril 10 mg QD"]), S(2, meds=["Lisinopril 10 mg QD", "Metformin 1000 mg BID"]))
    assert [d.category for d in r.deltas] == [C.DOSE_CHANGED]


def test_age_crossing_65():
    r = E.compute(S(1, when="2026-01-01", dob="1961-06-15"), S(2, when="2026-09-01", dob="1961-06-15"))
    assert r.deltas[0].rule_id == "R-AGE-65" and r.is_clinically_significant


def test_notes_and_race_are_noise():
    a = S(1, notes=["seen"]); b = S(2, notes=["patient seen today"])
    assert not E.compute(a, b).is_clinically_significant


def test_unmodelled_analyte_fails_safe():
    r = E.compute(S(1, labs={"Troponin": 0.02}), S(2, labs={"Troponin": 0.9}))
    assert r.deltas[0].classifier == "default" and r.is_clinically_significant and r.unclassified_count == 1


def test_first_encounter_has_no_deltas():
    r = E.compute(None, S(1))
    assert r.deltas == [] and r.previous_version is None


def test_threshold_is_configurable():
    hi = DeltaEngine(threshold=0.95)
    assert not hi.compute(S(1, labs={"eGFR": 72}), S(2, labs={"eGFR": 24})).is_clinically_significant
