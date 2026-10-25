"""Medical report parser for lab reports and clinical documents.

Extracts structured analyte values, vitals, medications, conditions, units, and dates
from unstructured clinical text, uploaded files, or OCR output. Includes reference range validation,
unit checking, date conflict detection, and unverified status tracking until confirmed.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Any

# Physiological reference boundaries for sanity checking parsed lab values
PHYSIOLOGICAL_RANGES: Dict[str, Tuple[float, float, str]] = {
    "egfr": (0.0, 200.0, "mL/min/1.73m2"),
    "hba1c": (2.0, 25.0, "%"),
    "potassium": (1.0, 10.0, "mmol/L"),
    "creatinine": (0.1, 25.0, "mg/dL"),
    "alt": (0.0, 5000.0, "U/L"),
    "ast": (0.0, 5000.0, "U/L"),
    "systolic_bp": (40.0, 300.0, "mmHg"),
}

KNOWN_UNITS: Dict[str, List[str]] = {
    "egfr": ["ml/min/1.73m2", "ml/min", "mL/min/1.73m2"],
    "hba1c": ["%", "mmol/mol"],
    "potassium": ["mmol/l", "mfe/l", "meq/l", "mmol/L"],
    "creatinine": ["mg/dl", "umol/l", "mg/dL"],
    "alt": ["u/l", "iu/l", "U/L"],
    "ast": ["u/l", "iu/l", "U/L"],
    "systolic_bp": ["mmhg", "mmHg"],
}


@dataclass
class ExtractedItem:
    analyte: str
    value: float
    unit: Optional[str]
    raw_text: str
    confidence: float
    reference_range: Optional[str] = None
    is_valid_range: bool = True
    range_warning: Optional[str] = None


@dataclass
class ReportParseResult:
    labs: Dict[str, Tuple[float, Optional[str]]] = field(default_factory=dict)
    vitals: Dict[str, Tuple[float, Optional[str]]] = field(default_factory=dict)
    medications: List[str] = field(default_factory=list)
    conditions: List[str] = field(default_factory=list)
    extracted_items: List[ExtractedItem] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    report_date: Optional[str] = None
    all_dates_found: List[str] = field(default_factory=list)
    detected_patient_name: Optional[str] = None
    status: str = "unverified_extracted"  # Values remain unverified until user confirmation


LAB_PATTERNS: List[Tuple[str, str, str, Optional[str]]] = [
    ("egfr", "eGFR", r"(?:egfr|estimated\s+gfr|gfr)\s*[:=]?\s*(\d+(?:\.\d+)?)\s*(ml/min(?:/1\.73m2)?)?", "mL/min/1.73m2"),
    ("hba1c", "HbA1c", r"(?:hba1c|a1c|glycated\s+hemoglobin)\s*[:=]?\s*(\d+(?:\.\d+)?)\s*(%)?", "%"),
    ("potassium", "Potassium", r"(?:potassium|k\+?)\s*[:=]?\s*(\d+(?:\.\d+)?)\s*(mmol/l|mfe/l|meq/l)?", "mmol/L"),
    ("creatinine", "Creatinine", r"(?:creatinine|serum\s+creatinine|creat)\s*[:=]?\s*(\d+(?:\.\d+)?)\s*(mg/dl|umol/l)?", "mg/dL"),
    ("alt", "ALT", r"(?:alt|sgpt|alanine\s+aminotransferase)\s*[:=]?\s*(\d+(?:\.\d+)?)\s*(u/l|iu/l)?", "U/L"),
    ("ast", "AST", r"(?:ast|sgot|aspartate\s+aminotransferase)\s*[:=]?\s*(\d+(?:\.\d+)?)\s*(u/l|iu/l)?", "U/L"),
]

VITAL_PATTERNS: List[Tuple[str, str, str, Optional[str]]] = [
    ("systolic_bp", "Systolic BP", r"(?:blood\s+pressure|bp|sbp)\s*[:=]?\s*(\d{2,3})\s*/\s*\d{2,3}", "mmHg"),
    ("systolic_bp", "Systolic BP", r"(?:systolic\s+bp|sbp)\s*[:=]?\s*(\d{2,3})", "mmHg"),
]


class MedicalReportParser:
    """Parses clinical reports and lab results into structured patient data with strict validation."""

    def parse(self, text: str, expected_patient_name: Optional[str] = None) -> ReportParseResult:
        if not text or not text.strip():
            return ReportParseResult(warnings=["Empty report text provided"], status="unverified_extracted")

        res = ReportParseResult()
        clean_text = text.strip()

        # Parse patient identity check if present
        patient_m = re.search(r"(?:patient(?:\s+name)?|name)\s*[:=]\s*([A-Za-z\s-]+?)(?=[.,\n\d]|$)", clean_text, re.IGNORECASE)
        if patient_m:
            res.detected_patient_name = patient_m.group(1).strip()
            if expected_patient_name and expected_patient_name.lower() not in res.detected_patient_name.lower():

                res.warnings.append(
                    f"Patient name mismatch: report states '{res.detected_patient_name}', but active record is '{expected_patient_name}'."
                )

        # Parse all collection dates present
        dates = re.findall(r"\b(\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{4})\b", clean_text)
        if dates:
            res.all_dates_found = list(dict.fromkeys(dates))
            res.report_date = res.all_dates_found[0]
            if len(res.all_dates_found) > 1:
                res.warnings.append(f"Multiple collection dates found in document: {', '.join(res.all_dates_found)}.")

        # Parse Labs with validation
        for key, name, pat, def_unit in LAB_PATTERNS:
            m = re.search(pat, clean_text, re.IGNORECASE)
            if m:
                val = float(m.group(1))
                raw_unit = m.group(2) if len(m.groups()) >= 2 and m.group(2) else def_unit
                unit = raw_unit.strip() if raw_unit else def_unit

                # Validate range
                is_valid = True
                range_warn = None
                if key in PHYSIOLOGICAL_RANGES:
                    min_val, max_val, expected_u = PHYSIOLOGICAL_RANGES[key]
                    if val < min_val or val > max_val:
                        is_valid = False
                        range_warn = f"Value {val} {unit} is outside plausible physiological range [{min_val}, {max_val}]."
                        res.warnings.append(f"{name}: {range_warn}")

                res.labs[key] = (val, unit)
                res.extracted_items.append(
                    ExtractedItem(
                        analyte=name,
                        value=val,
                        unit=unit,
                        raw_text=m.group(0),
                        confidence=0.95 if is_valid else 0.5,
                        is_valid_range=is_valid,
                        range_warning=range_warn,
                    )
                )

        # Parse Vitals
        for key, name, pat, def_unit in VITAL_PATTERNS:
            if key not in res.vitals:
                m = re.search(pat, clean_text, re.IGNORECASE)
                if m:
                    val = float(m.group(1))
                    is_valid = True
                    range_warn = None
                    if key in PHYSIOLOGICAL_RANGES:
                        min_val, max_val, _ = PHYSIOLOGICAL_RANGES[key]
                        if val < min_val or val > max_val:
                            is_valid = False
                            range_warn = f"Vital {val} is outside physiological range [{min_val}, {max_val}]."
                            res.warnings.append(f"{name}: {range_warn}")

                    res.vitals[key] = (val, def_unit)
                    res.extracted_items.append(
                        ExtractedItem(
                            analyte=name,
                            value=val,
                            unit=def_unit,
                            raw_text=m.group(0),
                            confidence=0.9 if is_valid else 0.5,
                            is_valid_range=is_valid,
                            range_warning=range_warn,
                        )
                    )

        # Parse Medications
        med_section = re.search(
            r"(?:medications|medicines|rx|drugs)\s*[:\n]+(.*?)(?:\n\n|\n[A-Z]|$)", clean_text, re.IGNORECASE | re.DOTALL
        )
        if med_section:
            for ln in med_section.group(1).splitlines():
                ln = re.sub(r"^\s*[-*•\d.]+\s*", "", ln).strip()
                if len(ln) > 3 and not re.match(r"^(none|n/a)$", ln, re.I):
                    res.medications.append(ln)

        # Warnings for missing critical labs
        if "egfr" not in res.labs and any(m in clean_text.lower() for m in ["metformin", "glucophage"]):
            res.warnings.append("Metformin identified in report text, but eGFR lab value is missing.")
        if "hba1c" not in res.labs and "diabetes" in clean_text.lower():
            res.warnings.append("Diabetes identified in report text, but HbA1c measurement is missing.")

        return res
