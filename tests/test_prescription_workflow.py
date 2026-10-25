from datetime import datetime, timezone
import pytest
from src.contracts.recommendation import DraftPrescription

def test_draft_prescription_creation_and_signoff():
    draft = DraftPrescription(
        prescription_id="RX-DRAFT-1001",
        medication="Metformin",
        formulation="Tablet",
        strength="500 mg",
        dosage="500 mg",
        route="oral",
        frequency="BID with meals",
        duration="90 days",
        sig="Take 1 tablet twice daily with meals",
        status="draft",
        allergy_recheck_status="checked_safe",
        interaction_recheck_status="checked_safe",
        safety_warnings=["Monitor eGFR every 6 months."]
    )

    assert draft.status == "draft"
    assert draft.signed_by is None
    assert draft.signed_at is None

    # Simulate clinician authorization
    now = datetime.now(timezone.utc)
    signed = draft.model_copy(update={
        "status": "signed",
        "signed_by": "DR-SMITH-01",
        "signed_at": now,
        "digital_signature": "SIG-HMAC-SHA256-992F",
        "audit_notes": "Reviewed eGFR (55) and confirmed appropriate."
    })

    assert signed.status == "signed"
    assert signed.signed_by == "DR-SMITH-01"
    assert signed.signed_at is not None
    assert signed.digital_signature is not None

def test_patient_visibility_filtering():
    prescriptions = [
        DraftPrescription(
            prescription_id="RX-DRAFT-01",
            medication="Lisinopril",
            dosage="10 mg",
            frequency="Daily",
            sig="Take 1 tablet daily",
            status="draft"
        ),
        DraftPrescription(
            prescription_id="RX-SIGNED-02",
            medication="Metformin",
            dosage="500 mg",
            frequency="BID",
            sig="Take 1 tablet twice daily",
            status="signed",
            signed_by="DR-JONES"
        )
    ]

    # Patients can ONLY see signed prescriptions
    patient_visible = [p for p in prescriptions if p.status == "signed"]
    assert len(patient_visible) == 1
    assert patient_visible[0].prescription_id == "RX-SIGNED-02"
