"""Recommendation, RiskFlag, ValidationReport contracts.
Implements Clinical Safety (AGENTS.md Rule 1), Validation (A.3 G3), Calibration (G4).
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, model_validator

from src.contracts.delta import DeltaReport
from src.contracts.evidence import ClinicalClaim, EvidenceRef


class RiskSeverity(str, Enum):
    LOW = "low"
    MODERATE = "moderate"
    HIGH = "high"
    CRITICAL = "critical"


class RiskFlag(BaseModel):
    """Identified clinical risk (drug interaction, contraindication, organ impairment)."""
    flag_id: str = Field(..., description="Stable risk identifier, e.g. RF-ALLERGY-PENICILLIN-AMOXICILLIN")
    severity: RiskSeverity
    category: str = Field(..., description="drug_interaction | contraindication | allergy | organ_adjustment | beers | hepatic | pregnancy | qt | data_quality")
    description: str = Field(..., min_length=5)
    evidence: List[EvidenceRef] = Field(default_factory=list)
    mitigation_advice: Optional[str] = None
    claim_id: Optional[str] = Field(None, description="Claim that asserts this flag")
    involved: List[str] = Field(default_factory=list, description="Drugs/conditions involved (normalised)")
    source_agent: Optional[str] = None
    source_type: str = Field(default="rule", description="rule | llm")


class ValidationFinding(BaseModel):
    check: str
    detail: str
    claim_id: Optional[str] = None
    severity: str = "high"  # high => defer, medium => block claim only
    agent: Optional[str] = None


class ValidationReport(BaseModel):
    """Adversarial cross-check of pipeline claims and consistency."""
    report_id: str
    passed: bool
    internal_contradictions: List[str] = Field(default_factory=list)
    guideline_conflicts: List[str] = Field(default_factory=list)
    unsupported_claims: List[str] = Field(default_factory=list)
    fault_detected: bool = False
    containment_status: str = Field(default="clean", description="clean | contained | escaped")
    findings: List[ValidationFinding] = Field(default_factory=list)
    blocked_claim_ids: List[str] = Field(default_factory=list)
    unsafe_upstream: bool = Field(default=False, description="Fact-level upstream corruption: withhold + escalate")
    checks_run: int = 0


class RecommendationStatus(str, Enum):
    REQUIRES_CLINICIAN_REVIEW = "requires_clinician_review"
    CLINICIAN_APPROVED = "clinician_approved"
    CLINICIAN_MODIFIED = "clinician_modified"
    REJECTED = "rejected"
    ESCALATED = "escalated"


class ClinicalAction(BaseModel):
    """Proposed clinical action for clinician review and sign-off."""
    action_type: str = Field(..., description="prescribe | discontinue | adjust_dose | review | order_lab | monitor | avoid | consult")
    target: str
    details: str
    rationale: str
    urgency: str = "routine"
    source_agent: Optional[str] = None
    claim_ids: List[str] = Field(default_factory=list, description="Claims justifying this action (Rule 4 applies to actions)")
    options: List[str] = Field(default_factory=list, description="Pros/cons or alternatives considered")
    source_type: str = Field(default="rule", description="rule | llm")


class DraftPrescription(BaseModel):
    """Draft prescription generated for clinician verification and digital sign-off."""
    prescription_id: str = Field(..., description="Unique draft prescription ID, e.g. RX-DRAFT-8A2F")
    medication: str = Field(..., description="Generic or brand medication name")
    formulation: Optional[str] = Field(None, description="e.g. tablet, capsule, oral solution")
    strength: Optional[str] = Field(None, description="e.g. 500 mg")
    dosage: str = Field(..., description="e.g. 500 mg")
    route: str = Field(default="oral", description="oral | IV | IM | topical | sublingual | inhalation")
    frequency: str = Field(..., description="e.g. twice daily with meals (BID)")
    duration: str = Field(default="90 days", description="Treatment duration")
    sig: str = Field(..., description="Directions for patient label")
    allergy_recheck_status: str = Field(default="checked_safe", description="checked_safe | conflict_flagged")
    interaction_recheck_status: str = Field(default="checked_safe", description="checked_safe | conflict_flagged")
    status: str = Field(default="draft", description="draft | signed | rejected | cancelled")
    signed_by: Optional[str] = None
    signed_at: Optional[datetime] = None
    digital_signature: Optional[str] = None
    audit_notes: Optional[str] = None
    safety_warnings: List[str] = Field(default_factory=list)



class ConflictRecord(BaseModel):
    agents: List[str]
    position_a: str
    position_b: str
    evidence_a: List[str] = Field(default_factory=list)
    evidence_b: List[str] = Field(default_factory=list)


class AgentTraceEntry(BaseModel):
    agent: str
    status: str = Field(..., description="ran | reused_cached | skipped_carry_forward | abstained")
    reason: str = ""
    confidence: Optional[float] = None
    latency_ms: float = 0.0
    n_claims: int = 0


class RunMetrics(BaseModel):
    reasoning_invocations: int = 0
    context_invocations: int = 0
    safety_invocations: int = 0
    agent_invocations: int = 0
    wall_clock_ms: float = 0.0
    cycles: int = 0
    safety_replans: int = 0


class Recommendation(BaseModel):
    """Decision-support output. Rule 1: never final without clinician approval."""
    recommendation_id: str
    patient_id: str
    encounter_id: Optional[str] = None
    state_version: Optional[int] = None
    execution_mode: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    requires_clinician_review: bool = True
    status: RecommendationStatus = RecommendationStatus.REQUIRES_CLINICIAN_REVIEW

    clinical_actions: List[ClinicalAction] = Field(default_factory=list)
    draft_prescriptions: List[DraftPrescription] = Field(default_factory=list)
    supported_claims: List[ClinicalClaim] = Field(default_factory=list)
    flagged_claims: List[ClinicalClaim] = Field(default_factory=list, description="Blocked by provenance gate/validation; shown to clinician, NOT approved")
    flagged_actions: List[ClinicalAction] = Field(default_factory=list)
    risk_flags: List[RiskFlag] = Field(default_factory=list)
    flagged_risk_flags: List[RiskFlag] = Field(default_factory=list)
    validation_report: Optional[ValidationReport] = None
    conflicts: List[ConflictRecord] = Field(default_factory=list)
    stale_evidence_ids: List[str] = Field(default_factory=list)

    deferred: bool = Field(default=False, description="System abstained: findings shown, no recommendation")
    abstention_reason: Optional[str] = None
    aggregate_confidence: Optional[float] = None
    calibrated_confidence: Optional[float] = Field(None, description="P(correct) from the fitted logistic calibrator, if one is configured")
    confidence_components: Dict[str, float] = Field(default_factory=dict)
    llm_escalation: Optional[Dict[str, Any]] = Field(None, description=(
        "Set only when this case was hard enough to escalate to an LLM for a secondary opinion (see "
        "src/orchestrator/escalation.py). ADVISORY ONLY: never contributes to clinical_actions, risk_flags, "
        "aggregate_confidence or the abstention decision - those remain rule-based. Every 'considerations' "
        "entry passed the same evidence-linking check (src/provenance/claim_extractor.py EvidenceLinker) as "
        "every other agent's claims; unsupported model output is dropped, counted, never shown to the clinician "
        "as fact. Keys: triggered (bool), status ('ok'|'unavailable'|'not_triggered'), trigger_reason, model, "
        "considerations (List[str], evidence-linked only), n_proposed, n_dropped_unsupported."))
    emergency_flags: List[str] = Field(default_factory=list)
    carried_forward_from: Optional[str] = None
    what_changed: List[str] = Field(default_factory=list)
    delta_report: Optional[DeltaReport] = None
    trace: List[AgentTraceEntry] = Field(default_factory=list)
    metrics: RunMetrics = Field(default_factory=RunMetrics)

    explanation: str = Field(..., min_length=5)

    reviewed_by: Optional[str] = None
    reviewed_at: Optional[datetime] = None
    review_notes: Optional[str] = None

    @model_validator(mode="after")
    def enforce_safety_governance(self) -> "Recommendation":
        if self.status in (RecommendationStatus.CLINICIAN_APPROVED, RecommendationStatus.CLINICIAN_MODIFIED):
            if self.requires_clinician_review:
                raise ValueError("An approved recommendation must set requires_clinician_review=False.")
            if not self.reviewed_by:
                raise ValueError("An approved recommendation must document reviewed_by.")
        return self
