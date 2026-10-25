# Data

* `data/knowledge/` - curated evidence corpus and drug tables (see its README).
* `eval/scenarios/adaptation.yaml` - 10 hand-authored change families + a negative control (ground truth for adaptation recall).
* `eval/harness/cohort.py` - seeded synthetic longitudinal simulator (`python run.py cohort --count 500`). It is **not Synthea**.
* `src/ingest/fhir_loader.py`: `bundle_to_states(bundle_dict)` turns a FHIR R4 bundle (Patient, Condition, MedicationRequest, AllergyIntolerance, Observation with LOINC codes) into one `PatientState` per observation date. **Tested only on a hand-written bundle**; adapt the LOINC map / medication extraction for real Synthea output.
* `eval/scenarios/holdout_adaptation.yaml` (13 held-out scenarios) and `linker_pairs.yaml` (labelled evidence-linking pairs, dev/test/test2).
* No real patient data is used anywhere (AGENTS.md Rule 2). The API rejects obvious identifiers.
