import pytest
from src.contracts.evidence import ClaimStatus
from src.knowledge import get_evidence_store
from src.provenance import ClaimExtractor, EvidenceLinker, ProvenanceGate
from src.retrieval import LocalKnowledgeRetriever, expand_query, PubMedRetriever


def test_query_expansion_adds_medical_synonyms():
    exp = expand_query("metformin eGFR 25")
    assert "kidney" in exp and "renal" in exp and "glomerular" in exp


def test_hybrid_rag_retrieval_relevance_and_ranking():
    r = LocalKnowledgeRetriever()
    # "eGFR" query expands and utilizes dense TF-IDF vector similarity
    results = r.retrieve("metformin eGFR 25", top_k=3)
    assert len(results) > 0
    assert results[0].id.startswith("EV-FDA-METFORMIN") or results[0].id.startswith("EV-KDIGO-DM-CKD-2022-METFORMIN")
    assert all(0 < x.retrieval_score <= 1.0 for x in results)


def test_contradiction_detection_flags_conflicting_claims():
    lk = EvidenceLinker()
    snippet = "Metformin is contraindicated in patients with an estimated glomerular filtration rate (eGFR) below 30 mL/min/1.73 m2."
    
    # Permission claim vs prohibition snippet = contradiction
    assert lk.is_contradiction("Metformin is safe when eGFR is below 30", snippet)
    # Threshold mismatch claim = contradiction
    assert lk.is_contradiction("Metformin is contraindicated when eGFR is below 45", snippet)


def test_provenance_gate_verification_report():
    store = get_evidence_store()
    pool = [c.ref for c in store.chunks.values()]
    lk, ex = EvidenceLinker(), ClaimExtractor()
    
    claims = ex.extract("Metformin should be discontinued if eGFR falls below 30. Metformin is safe at eGFR 10.", "test_agent")
    linked = lk.link(claims, pool)
    
    report = ProvenanceGate.compute_verification_report(linked)
    assert report["total"] == 2
    assert report["supported"] >= 1
    assert "support_rate" in report


def test_connector_fallback_when_enabled():
    pm = PubMedRetriever(allow_fallback=True)
    res = pm.retrieve("metformin eGFR", top_k=2)
    assert len(res) > 0
