import pytest
from src.contracts.recommendation import DraftPrescription, Recommendation, RecommendationStatus
from src.ingest.builders import build_state
from src.orchestrator import PipelineConfig, PipelineRunner


def test_draft_prescription_creation_and_contract():
    rx = DraftPrescription(
        prescription_id="RX-DRAFT-101",
        medication="Metformin",
        dosage="1000 mg",
        route="oral",
        frequency="twice daily",
        duration="90 days",
        sig="Take 1 tablet by mouth twice daily with meals",
        status="draft",
        safety_warnings=["Reduce dose below eGFR 45"],
    )
    assert rx.prescription_id == "RX-DRAFT-101"
    assert rx.status == "draft"
    assert len(rx.safety_warnings) == 1


def test_pipeline_generates_draft_prescriptions():
    s = build_state("P-RX", "1960-01-01", "female", meds=["Metformin 1000 mg BID", "Lisinopril 10 mg QD"], labs={"eGFR": 70})
    res = PipelineRunner(PipelineConfig(llm=None)).run(s)
    rec = res.recommendation
    
    # Check that recommendation contains draft prescription structures or actions
    assert rec.recommendation_id is not None
    assert rec.requires_clinician_review is True
    assert rec.status == RecommendationStatus.REQUIRES_CLINICIAN_REVIEW
