"""Patient, Encounter, and longitudinal state contracts.
Implements Longitudinal State Store & Versioning, Part A.2 & C.2.
"""

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, ConfigDict, Field, model_validator


class PatientDemographics(BaseModel):
    """Demographic characteristics."""
    birth_date: str = Field(..., description="Date of birth (YYYY-MM-DD)")
    gender: str = Field(..., description="Administrative gender")
    race: Optional[str] = Field(None, description="Reported race")
    ethnicity: Optional[str] = Field(None, description="Reported ethnicity")


class LabValue(BaseModel):
    """Quantitative or qualitative laboratory observation."""
    code: str = Field(..., description="LOINC or local test code")
    display_name: str = Field(..., description="Human-readable lab name, e.g. 'eGFR'")
    value: float = Field(..., description="Numeric measurement value")
    unit: str = Field(..., description="Standardized measurement unit, e.g. 'mL/min/1.73m2'")
    reference_low: Optional[float] = Field(None, description="Lower normal reference limit")
    reference_high: Optional[float] = Field(None, description="Upper normal reference limit")
    timestamp: datetime = Field(..., description="Specimen collection or observation timestamp")


class VitalSign(BaseModel):
    """Physiological vital sign measurement."""
    code: str = Field(..., description="Measurement identifier or LOINC")
    display_name: str = Field(..., description="Human-readable vital name, e.g. 'Systolic BP'")
    value: float = Field(..., description="Numeric measurement value")
    unit: str = Field(..., description="Measurement unit, e.g. 'mmHg'")
    timestamp: datetime = Field(..., description="Measurement timestamp")


class Medication(BaseModel):
    """Active or historical medication record."""
    name: str = Field(..., description="Medication brand or generic name")
    rxnorm_code: Optional[str] = Field(None, description="Standardized RxCUI code")
    dosage: str = Field(..., description="Dose description, e.g. '500 mg'")
    route: Optional[str] = Field("oral", description="Route of administration")
    frequency: Optional[str] = Field("daily", description="Frequency of administration")
    start_date: datetime = Field(..., description="Start of therapy")
    end_date: Optional[datetime] = Field(None, description="Discontinuation date")
    is_active: bool = Field(default=True, description="Whether the medication is currently active")


class Allergy(BaseModel):
    """Patient allergy or clinical intolerance."""
    substance: str = Field(..., description="Allergen substance or drug class")
    rxnorm_code: Optional[str] = Field(None, description="RxCUI if drug-related")
    reaction: Optional[str] = Field(None, description="Clinical manifestation of reaction")
    severity: str = Field(default="moderate", description="Mild, moderate, severe, or life-threatening")
    recorded_date: Optional[datetime] = Field(None, description="Record timestamp (no implicit now(): keeps hashing deterministic)")


class Condition(BaseModel):
    """Active or resolved medical condition / diagnosis."""
    code: str = Field(..., description="ICD-10 or SNOMED-CT code")
    display_name: str = Field(..., description="Clinical condition description")
    onset_date: Optional[datetime] = Field(None, description="Onset date")
    clinical_status: str = Field(default="active", description="active, recurrence, relapse, resolved")


class Encounter(BaseModel):
    """Clinical encounter record (FHIR / Synthea / MIMIC-IV)."""
    encounter_id: str = Field(..., description="Unique encounter identifier")
    patient_id: str = Field(..., description="Associated patient ID")
    encounter_class: str = Field(..., description="ambulatory, inpatient, emergency, wellness")
    start_time: datetime = Field(..., description="Encounter admission / start time")
    end_time: Optional[datetime] = Field(None, description="Encounter discharge / end time")
    reason: Optional[str] = Field(None, description="Chief complaint or reason for visit")
    diagnoses: List[Condition] = Field(default_factory=list, description="Encounter diagnoses")
    medications: List[Medication] = Field(default_factory=list, description="Medications prescribed/administered")
    observations: List[LabValue] = Field(default_factory=list, description="Labs and observations")


class PatientState(BaseModel):
    """Versioned, immutable-on-write snapshot of patient clinical state.
    Implements core requirement (a): PatientState is versioned and immutable-on-write.
    """
    model_config = ConfigDict(frozen=True)

    patient_id: str = Field(..., description="Patient identifier")
    version: int = Field(..., ge=1, description="Monotonically increasing version counter")
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), description="Snapshot creation timestamp")
    encounter_id: Optional[str] = Field(None, description="Encounter driving this state transition")
    demographics: PatientDemographics = Field(..., description="Patient demographic attributes")
    active_conditions: List[Condition] = Field(default_factory=list, description="Active diagnosed conditions")
    active_medications: List[Medication] = Field(default_factory=list, description="Currently active medications")
    allergies: List[Allergy] = Field(default_factory=list, description="Known drug & environmental allergies")
    latest_labs: Dict[str, LabValue] = Field(default_factory=dict, description="Keyed by lab display name")
    vitals: Dict[str, VitalSign] = Field(default_factory=dict, description="Keyed by vital sign name")
    procedures: List[str] = Field(default_factory=list, description="Recent clinical procedures")
    clinical_notes: List[str] = Field(default_factory=list, description="Narrative clinical text notes")
    previous_state_hash: Optional[str] = Field(None, description="Hash of the preceding state version")
    state_hash: Optional[str] = Field(None, description="Cryptographic content hash of this state version")

    @staticmethod
    def _canonical(values: dict) -> str:
        """Canonical JSON over ALL clinical content (labs, allergies, vitals, demographics...).

        Excluded on purpose: ``state_hash`` itself and ``timestamp`` (creation clock).
        """
        def enc(v):
            if isinstance(v, BaseModel):
                return v.model_dump(mode="json")
            return v
        clean = {k: v for k, v in values.items() if k not in ("state_hash", "timestamp")}
        payload = json.loads(json.dumps(clean, default=lambda o: enc(o) if isinstance(o, BaseModel) else str(o)))
        return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)

    @classmethod
    def compute_state_hash(cls, values: dict) -> str:
        """Deterministic SHA-256 over the full clinical content of a state."""
        return hashlib.sha256(cls._canonical(values).encode("utf-8")).hexdigest()

    def content_hash(self) -> str:
        return self.compute_state_hash(self.model_dump(mode="json"))

    def verify(self) -> bool:
        """True if the stored hash matches the content (tamper check)."""
        return self.state_hash is not None and self.state_hash == self.content_hash()
