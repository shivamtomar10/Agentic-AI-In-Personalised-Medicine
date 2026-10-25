"""Patient Follow-up & Clinician Alert Management.

Provides structured follow-up questionnaire ingestion, side-effect reporting,
monitoring plan tracking, and clinician alert acknowledgement with audit trail logging.
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field


class AlertSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class SideEffectReport(BaseModel):
    medication: str
    symptom: str
    severity: str = "moderate"
    onset_date: Optional[str] = None
    notes: Optional[str] = None


class PatientFollowUpResponse(BaseModel):
    followup_id: str
    patient_id: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    adherence_score: int = Field(..., ge=1, le=5, description="Medication adherence score (1-5)")
    side_effects: List[SideEffectReport] = Field(default_factory=list)
    symptom_progression: str = "unchanged"  # improved | unchanged | worsened
    concerns: Optional[str] = None
    requires_escalation: bool = False
    escalation_reason: Optional[str] = None


class ClinicianAlert(BaseModel):
    alert_id: str
    patient_id: str
    severity: AlertSeverity
    category: str
    message: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    acknowledged: bool = False
    acknowledged_by: Optional[str] = None
    acknowledged_at: Optional[datetime] = None
    action_taken: Optional[str] = None


class FollowUpManager:
    """Manages patient follow-up questionnaires and clinician alert notifications."""

    def __init__(self):
        self.followups: Dict[str, PatientFollowUpResponse] = {}
        self.alerts: Dict[str, ClinicianAlert] = {}

    def submit_followup(self, response: PatientFollowUpResponse) -> PatientFollowUpResponse:
        # Determine escalation rules
        if response.symptom_progression == "worsened" or any(s.severity == "severe" for s in response.side_effects):
            response.requires_escalation = True
            response.escalation_reason = "Patient reported worsening symptoms or severe medication side effects."

            # Create clinician alert
            alert = ClinicianAlert(
                alert_id=f"ALT-{response.followup_id}",
                patient_id=response.patient_id,
                severity=AlertSeverity.CRITICAL if any(s.severity == "severe" for s in response.side_effects) else AlertSeverity.WARNING,
                category="patient_followup_escalation",
                message=response.escalation_reason
            )
            self.alerts[alert.alert_id] = alert

        self.followups[response.followup_id] = response
        return response

    def acknowledge_alert(self, alert_id: str, clinician_staff_id: str, action_taken: str) -> ClinicianAlert:
        if alert_id not in self.alerts:
            raise ValueError(f"Alert {alert_id} not found")
        alert = self.alerts[alert_id]
        alert.acknowledged = True
        alert.acknowledged_by = clinician_staff_id
        alert.acknowledged_at = datetime.now(timezone.utc)
        alert.action_taken = action_taken
        return alert

    def get_pending_alerts(self, patient_id: Optional[str] = None) -> List[ClinicianAlert]:
        items = [a for a in self.alerts.values() if not a.acknowledged]
        if patient_id:
            items = [a for a in items if a.patient_id == patient_id]
        return items
