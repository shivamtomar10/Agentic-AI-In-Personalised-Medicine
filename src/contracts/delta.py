"""Delta and change categorization contracts.
Implements Temporal Personalization, DeltaEngine, Part A.2 & C.3.
"""

from datetime import datetime, timezone
from enum import Enum
from typing import Any, List, Optional
from pydantic import BaseModel, Field, model_validator


class DeltaCategory(str, Enum):
    """Clinically graded delta categories distinguishing true signal from analytical noise."""
    NEW_DIAGNOSIS = "NEW_DIAGNOSIS"
    RESOLVED_DIAGNOSIS = "RESOLVED_DIAGNOSIS"
    MEDICATION_ADDED = "MEDICATION_ADDED"
    MEDICATION_STOPPED = "MEDICATION_STOPPED"
    DOSE_CHANGED = "DOSE_CHANGED"
    NEW_ALLERGY = "NEW_ALLERGY"
    LAB_THRESHOLD_CROSSED = "LAB_THRESHOLD_CROSSED"
    LAB_TREND_SHIFT = "LAB_TREND_SHIFT"
    ORGAN_FUNCTION_CHANGE = "ORGAN_FUNCTION_CHANGE"
    VITAL_INSTABILITY = "VITAL_INSTABILITY"
    ALLERGY_REMOVED = "ALLERGY_REMOVED"
    DEMOGRAPHIC_CHANGE = "DEMOGRAPHIC_CHANGE"
    NOISE = "NOISE"


class Delta(BaseModel):
    """An individual typed, clinically graded difference between two patient states."""
    category: DeltaCategory = Field(..., description="Categorization of the detected change")
    field_path: str = Field(..., description="Dot-delimited path of the changed field e.g. 'labs.eGFR'")
    old_value: Optional[Any] = Field(None, description="Previous recorded value")
    new_value: Optional[Any] = Field(None, description="Current recorded value")
    significance: float = Field(..., ge=0.0, le=1.0, description="Clinical significance score in [0, 1]")
    rationale: str = Field(..., min_length=3, description="Clinical rationale or rule explanation")
    rule_id: Optional[str] = Field(None, description="Identifier of the rule that fired (if rule-driven)")
    classifier: str = Field(default="rule", description="rule | default (fail-safe, no rule matched) | llm")

    @model_validator(mode="after")
    def validate_noise_significance(self) -> "Delta":
        """NOISE category should strictly not have high significance."""
        if self.category == DeltaCategory.NOISE and self.significance > 0.1:
            raise ValueError(f"Deltas categorized as NOISE must have significance <= 0.1, got {self.significance}")
        return self


class DeltaReport(BaseModel):
    """Aggregate change report between two consecutive or comparative PatientState versions."""
    patient_id: str = Field(..., description="Patient identifier")
    previous_version: Optional[int] = Field(None, description="Previous PatientState version index")
    current_version: int = Field(..., description="Current PatientState version index")
    deltas: List[Delta] = Field(default_factory=list, description="List of detected differences")
    max_significance: float = Field(default=0.0, ge=0.0, le=1.0, description="Highest significance among all deltas")
    is_clinically_significant: bool = Field(default=False, description="True if max_significance >= threshold")
    threshold: float = Field(default=0.3, ge=0.0, le=1.0, description="Re-planning threshold used")
    unclassified_count: int = Field(default=0, description="Deltas classified by the fail-safe default, not a rule")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), description="Evaluation timestamp")

    @model_validator(mode="after")
    def calculate_summary_metrics(self) -> "DeltaReport":
        """Compute max_significance dynamically from deltas if not manually overridden."""
        if self.deltas:
            computed_max = max(d.significance for d in self.deltas)
            self.max_significance = round(computed_max, 4)
            self.is_clinically_significant = self.max_significance >= self.threshold
        else:
            self.max_significance = 0.0
            self.is_clinically_significant = False
        return self
