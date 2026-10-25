from src.contracts.evidence import ClaimStatus
from src.knowledge import get_drug_knowledge, get_evidence_store
from src.provenance import ClaimExtractor, EvidenceLinker
from src.retrieval import LocalKnowledgeRetriever, RetrievalUnavailableError, PubMedRetriever
import pytest

R = LocalKnowledgeRetriever()


def test_retrieval_ranks_relevant_chunk_first_and_scores_bounded():
    top = R.retrieve("metformin eGFR 24 contraindicated", 3)
    assert top[0].id.startswith("EV-FDA-METFORMIN") and all(0 < x.retrieval_score <= 1 for x in top)
    assert R.retrieve("penicillin allergy cephalosporin cross-reactivity", 1)[0].id.startswith("EV-JACI")
    assert R.retrieve("zzzz qqqq", 3) == []


def test_every_chunk_resolves_and_char_span_matches():
    st = get_evidence_store()
    for cid, ch in st.chunks.items():
        s, e = ch.ref.char_span
        assert ch.text[s:e].strip() == ch.ref.snippet.strip() and st.resolve(ch.ref)


def test_online_connectors_fail_loudly_not_silently():
    with pytest.raises(RetrievalUnavailableError):
        PubMedRetriever().retrieve("x")
    with pytest.raises(RetrievalUnavailableError):
        R.by_topic("no_such_topic")


def test_drug_normalisation_and_tables():
    dk = get_drug_knowledge()
    assert dk.normalise("Augmentin 875 mg") == "amoxicillin-clavulanate" and dk.normalise("Metformin Hydrochloride ER 500") == "metformin"
    assert dk.allergy_conflict("Penicillin (rash)", "amoxicillin") == "definite" and dk.allergy_conflict("penicillin", "cephalexin") == "possible"
    assert dk.allergy_conflict("penicillin", "metformin") is None
    pairs = {(i.drug_a, i.drug_b) for i in dk.interactions(["warfarin", "ibuprofen", "ondansetron", "azithromycin", "metformin"])}
    assert pairs == {("ibuprofen", "warfarin"), ("azithromycin", "ondansetron")}


def test_linker_binds_true_claims_and_rejects_fabricated_or_misquoted():
    pool = [c.ref for c in get_evidence_store().chunks.values()]
    lk, ex = EvidenceLinker(), ClaimExtractor()
    def status(t): return lk.link(ex.extract(t, "x"), pool)[0].status
    assert status("Metformin should be discontinued if eGFR falls below 30") == ClaimStatus.SUPPORTED
    assert status("Metformin is lethal when combined with normal saline") == ClaimStatus.UNSUPPORTED
    assert status("Metformin is contraindicated when eGFR is below 45") == ClaimStatus.UNSUPPORTED     # wrong threshold


def test_extractor_splits_atomic_claims():
    cs = ClaimExtractor().extract("Metformin is contraindicated below eGFR 30. Warfarin interacts with ibuprofen; monitor INR closely", "x")
    assert len(cs) == 3


def test_linker_measured_on_labelled_pairs_regression():
    """Guards the dev-set behaviour only. (test/test2 are reported in docs/results.md; test2 shows the linker is still fooled by
    antonym adjectives, 'type 1' vs 'type 2', unknown words and re-used numbers, so it is NOT asserted perfect.)"""
    from eval.harness.linker_eval import evaluate
    r = evaluate()
    assert r["dev"]["precision"] >= 0.9 and r["dev"]["recall"] >= 0.8
    assert r["test2"]["precision"] < 1.0        # documents the known limitation; if this starts failing, celebrate and update the docs


def test_verbatim_multi_clause_evidence_always_supports_itself():
    lk = EvidenceLinker()
    for ch in get_evidence_store().chunks.values():
        assert lk.entails(ch.ref.snippet.rstrip("."), ch.ref.snippet), ch.ref.id
