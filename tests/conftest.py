import pytest

from src.ingest.builders import build_state


@pytest.fixture
def T2D():
    """Baseline diabetic/hypertensive patient factory."""
    def make(version=1, when="2026-01-10", **kw):
        d = dict(conditions=["Type 2 diabetes mellitus (E11.9)", "Hypertension (I10)"], meds=["Metformin 1000 mg BID", "Lisinopril 20 mg QD"],
                 labs={"eGFR": 72, "Potassium": 4.4, "HbA1c": 6.9}, vitals={"SBP": 124})
        d.update(kw)
        return build_state("P1", "1958-05-10", "male", timestamp=when, version=version, **d)
    return make


@pytest.fixture(scope="session")
def runner():
    from src.orchestrator import PipelineRunner
    return PipelineRunner()
