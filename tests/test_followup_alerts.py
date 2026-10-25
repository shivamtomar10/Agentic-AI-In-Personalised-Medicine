import pytest
from src.state.followup import (
    FollowUpManager, PatientFollowUpResponse, SideEffectReport, AlertSeverity
)

def test_patient_followup_and_alert_acknowledgement():
    mgr = FollowUpManager()

    # Submit routine follow-up
    res1 = mgr.submit_followup(PatientFollowUpResponse(
        followup_id="FUP-001",
        patient_id="PT-001",
        adherence_score=5,
        symptom_progression="improved"
    ))
    assert res1.requires_escalation is False
    assert len(mgr.get_pending_alerts()) == 0

    # Submit follow-up with severe side effect -> triggers critical alert
    res2 = mgr.submit_followup(PatientFollowUpResponse(
        followup_id="FUP-002",
        patient_id="PT-001",
        adherence_score=3,
        side_effects=[SideEffectReport(medication="Metformin", symptom="Severe Nausea & Vomiting", severity="severe")],
        symptom_progression="worsened"
    ))
    assert res2.requires_escalation is True
    assert len(mgr.get_pending_alerts()) == 1

    alert = mgr.get_pending_alerts()[0]
    assert alert.severity == AlertSeverity.CRITICAL
    assert alert.patient_id == "PT-001"

    # Clinician acknowledges alert
    ack = mgr.acknowledge_alert(alert.alert_id, "DR-SMITH-01", "Dose reduced to 500 mg QD and antiemetic prescribed.")
    assert ack.acknowledged is True
    assert ack.acknowledged_by == "DR-SMITH-01"
    assert len(mgr.get_pending_alerts()) == 0
