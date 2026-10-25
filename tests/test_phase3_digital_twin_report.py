import pytest
from src.ingest.report_parser import MedicalReportParser


def test_medical_report_parser_extracts_labs_and_vitals():
    text = """
    PATIENT LAB REPORT - 2026-09-15
    eGFR: 42 mL/min/1.73m2
    HbA1c: 7.8 %
    Potassium: 5.4 mmol/L
    ALT: 135 U/L
    BP: 138/84
    Medications:
    - Metformin 1000 mg BID
    - Lisinopril 20 mg QD
    """
    parser = MedicalReportParser()
    res = parser.parse(text)
    
    assert "egfr" in res.labs and res.labs["egfr"][0] == 42.0
    assert "hba1c" in res.labs and res.labs["hba1c"][0] == 7.8
    assert "potassium" in res.labs and res.labs["potassium"][0] == 5.4
    assert "alt" in res.labs and res.labs["alt"][0] == 135.0
    assert "systolic_bp" in res.vitals and res.vitals["systolic_bp"][0] == 138.0
    assert len(res.medications) >= 2
    assert res.report_date == "2026-09-15"


def test_medical_report_parser_flags_missing_egfr_warning():
    text = "Medications: Metformin 1000 mg BID\nHbA1c: 8.2%"
    parser = MedicalReportParser()
    res = parser.parse(text)
    
    assert len(res.warnings) >= 1
    assert "Metformin" in res.warnings[0] and "eGFR" in res.warnings[0]


def test_api_extract_report_endpoint():
    parser = MedicalReportParser()
    parsed = parser.parse("eGFR: 55 mL/min/1.73m2, HbA1c 6.9%")
    assert parsed.labs["egfr"][0] == 55.0
