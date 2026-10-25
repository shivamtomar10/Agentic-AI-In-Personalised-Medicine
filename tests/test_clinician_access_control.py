import pytest
from src.auth.db import Database
from src.auth.service import AuthService

def test_patient_assignment_and_account_deletion():
    db = Database(":memory:")
    svc = AuthService(db, invite_code="TEST-INVITE")

    # Create clinician and patient accounts
    clinician = svc.register_clinician(
        email="dr.smith@hospital.org",
        password="SecurePassword123!",
        display_name="Dr. Smith",
        staff_id="DR-SMITH-01",
        invite_code="TEST-INVITE",
        accept_terms=True
    )

    patient = svc.register_patient(
        email="patient.doe@example.com",
        password="PatientPass123!",
        display_name="Jane Doe",
        birth_date="1980-05-15",
        gender="female",
        accept_terms=True
    )

    pid = patient.patient_id
    assert pid is not None

    # Initially unassigned
    assert not svc.is_patient_assigned(clinician.id, pid)
    assert svc.get_assigned_patients(clinician.id) == []

    # Assign patient
    svc.assign_patient(clinician.id, pid)
    assert svc.is_patient_assigned(clinician.id, pid)
    assert svc.get_assigned_patients(clinician.id) == [pid]

    # Unassign patient
    svc.unassign_patient(clinician.id, pid)
    assert not svc.is_patient_assigned(clinician.id, pid)

    # Account deletion (GDPR erasure)
    deleted = svc.delete_account(patient.id)
    assert deleted is True
    assert svc.find_by_email("patient.doe@example.com") is None
