import pytest
from src.ingest.report_parser import MedicalReportParser, ReportParseResult

def test_physiological_range_validation():
    parser = MedicalReportParser()

    # Normal lab report
    text_normal = "eGFR: 45 mL/min/1.73m2, HbA1c: 7.2%, Potassium: 4.5 mmol/L"
    res_normal = parser.parse(text_normal)
    assert res_normal.labs["egfr"][0] == 45.0
    assert len(res_normal.warnings) == 0

    # Implausible lab report (eGFR = 999)
    text_extreme = "eGFR: 999 mL/min/1.73m2, HbA1c: 2.1%"
    res_extreme = parser.parse(text_extreme)
    assert len(res_extreme.warnings) > 0
    assert any("outside plausible physiological range" in w for w in res_extreme.warnings)
    assert res_extreme.extracted_items[0].is_valid_range is False

def test_multiple_dates_detection():
    parser = MedicalReportParser()
    text = "Report Date: 2026-03-01. Previous Collection Date: 2025-11-15. eGFR: 50"
    res = parser.parse(text)
    assert len(res.all_dates_found) == 2
    assert any("Multiple collection dates found" in w for w in res.warnings)

def test_patient_name_matching():
    parser = MedicalReportParser()
    text = "Patient Name: John Doe. eGFR: 60"
    res = parser.parse(text, expected_patient_name="Jane Smith")
    assert res.detected_patient_name == "John Doe"
    assert any("Patient name mismatch" in w for w in res.warnings)

def test_unverified_status_flag():
    parser = MedicalReportParser()
    text = "eGFR: 55, HbA1c: 6.8%"
    res = parser.parse(text)
    assert res.status == "unverified_extracted"
