import pytest
from src.retrieval.connectors import PubMedRetriever, OpenFDARetriever, RxNormRetriever, GuidelineRetriever
from src.retrieval.local import RetrievalUnavailableError
from src.contracts.evidence import SourceType


def test_pubmed_retriever_offline_fallback():
    retriever = PubMedRetriever(allow_fallback=True, offline_only=True)
    results = retriever.retrieve("metformin eGFR contraindication", top_k=2)
    assert len(results) > 0
    assert results[0].source == SourceType.PUBMED
    assert results[0].retrieval_timestamp is not None
    assert results[0].verification_status in ("supported", "conflicting")

def test_openfda_retriever_offline_fallback():
    retriever = OpenFDARetriever(allow_fallback=True, offline_only=True)
    results = retriever.retrieve("lisinopril pregnancy", top_k=2)
    assert len(results) > 0
    assert results[0].source == SourceType.OPENFDA

def test_rxnorm_retriever_offline_fallback():
    retriever = RxNormRetriever(allow_fallback=True, offline_only=True)
    results = retriever.retrieve("metformin", top_k=1)
    assert len(results) > 0
    assert results[0].source == SourceType.RXNORM

def test_retriever_no_fallback_raises():
    retriever = PubMedRetriever(allow_fallback=False, offline_only=False)
    with pytest.raises(RetrievalUnavailableError):
        retriever.retrieve("test", top_k=1)

