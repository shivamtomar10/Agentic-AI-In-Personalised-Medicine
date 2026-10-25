import pytest

from src.state import IntegrityError, PatientStateStore
from src.ingest.builders import build_state


def test_versions_chain_and_verify():
    st = PatientStateStore()
    a = st.append_state(build_state("P", labs={"eGFR": 60}))
    b = st.append_state(build_state("P", labs={"eGFR": 50}))
    assert (a.version, b.version) == (1, 2) and b.previous_state_hash == a.state_hash and st.verify_chain("P")
    assert st.get_by_hash(a.state_hash).version == 1 and st.latest("P").version == 2


def test_returned_copies_cannot_corrupt_store():
    st = PatientStateStore()
    st.append_state(build_state("P", meds=["Metformin 500 mg BID"]))
    got = st.latest("P")
    got.active_medications.append(got.active_medications[0])   # mutate the copy
    assert len(st.latest("P").active_medications) == 1 and st.verify_chain("P")


def test_tamper_detection():
    st = PatientStateStore()
    st.append_state(build_state("P", labs={"eGFR": 60}))
    st._data["P"][0].latest_labs["eGFR"].value = 999.0
    with pytest.raises(IntegrityError):
        st.verify_chain("P")


def test_diff_and_persistence(tmp_path):
    f = tmp_path / "s.jsonl"
    st = PatientStateStore(persist_path=str(f))
    a = st.append_state(build_state("P", labs={"eGFR": 60}))
    b = st.append_state(build_state("P", labs={"eGFR": 50}, allergies=["Penicillin"]))
    d = st.diff(a, b)
    assert d["latest_labs.eGFR.value"] == (60.0, 50.0) and any("allergies" in k for k in d)
    assert PatientStateStore(persist_path=str(f)).verify_chain("P")
