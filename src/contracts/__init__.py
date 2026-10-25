"""Typed contracts package for Agentic AI in Personalized Medicine.
AGENTS.md Rule 3: Every agent input and output is a Pydantic v2 model in src/contracts/.
"""

from src.contracts.evidence import (
    SourceType,
    EvidenceRef,
    ClaimStatus,
    ClinicalClaim,
    ClaimPayload,
)
from src.contracts.delta import (
    DeltaCategory,
    Delta,
    DeltaReport,
)
from src.contracts.patient import (
    PatientDemographics,
    LabValue,
    VitalSign,
    Medication,
    Allergy,
    Condition,
    Encounter,
    PatientState,
)
from src.contracts.agent_io import (
    ExecutionMode,
    AgentInput,
    AgentOutput,
    DifferentialItem,
)
from src.contracts.recommendation import (
    RiskSeverity,
    RiskFlag,
    ValidationReport,
    RecommendationStatus,
    ClinicalAction,
    ConflictRecord,
    AgentTraceEntry,
    RunMetrics,
    ValidationFinding,
    Recommendation,
)

__all__ = [
    "ClaimPayload", "DifferentialItem", "ConflictRecord", "AgentTraceEntry", "RunMetrics", "ValidationFinding",
    "SourceType",
    "EvidenceRef",
    "ClaimStatus",
    "ClinicalClaim",
    "DeltaCategory",
    "Delta",
    "DeltaReport",
    "PatientDemographics",
    "LabValue",
    "VitalSign",
    "Medication",
    "Allergy",
    "Condition",
    "Encounter",
    "PatientState",
    "ExecutionMode",
    "AgentInput",
    "AgentOutput",
    "RiskSeverity",
    "RiskFlag",
    "ValidationReport",
    "RecommendationStatus",
    "ClinicalAction",
    "Recommendation",
]
