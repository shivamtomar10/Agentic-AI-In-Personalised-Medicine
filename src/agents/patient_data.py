"""PatientDataAgent: normalises the record into verifiable facts (units canonicalised, gaps flagged, never imputed)."""
from __future__ import annotations

from src.agents.base import ClinicalAgent
from src.agents.common import ehr_ref, mk_claim
from src.contracts.agent_io import AgentInput, AgentOutput
from src.knowledge.drugs import get_drug_knowledge
from src.knowledge.patient_view import PatientView
from src.units import canonical_unit


class PatientDataAgent(ClinicalAgent):
    ROLE = "Aggregate and normalise patient data; flag missing critical fields (no imputation)"
    READS = ("demographics", "conditions", "medications", "allergies", "labs", "vitals")

    def __init__(self, **kw):
        super().__init__("patient_data", **kw)

    def _run(self, inp: AgentInput) -> AgentOutput:
        st = inp.patient_state
        pv = PatientView(st)
        dk = get_drug_knowledge()
        A, pid, ver = self.name, st.patient_id, st.version
        claims = []

        def ref(path, text):
            return [ehr_ref(pid, ver, path, text)]
        for a, v in sorted(pv.labs.items()):
            claims.append(mk_claim(A, f"Lab {a} = {v} {canonical_unit(a)}", ref(f"labs.{a}", f"{a}={v} {canonical_unit(a)}"), 0.99,
                                   "lab_value", {"analyte": a, "value": v, "unit": canonical_unit(a)}))
        for a, v in sorted(pv.vitals.items()):
            claims.append(mk_claim(A, f"Vital {a} = {v} {canonical_unit(a)}", ref(f"vitals.{a}", f"{a}={v}"), 0.99,
                                   "vital_value", {"analyte": a, "value": v, "unit": canonical_unit(a)}))
        for s in sorted(pv.allergies):
            claims.append(mk_claim(A, f"Documented allergy: {s}", ref(f"allergies.{s}", f"allergy {s}"), 0.99, "allergy_fact", {"substance": s}))
        for m in pv.meds:
            claims.append(mk_claim(A, f"Active medication: {m}", ref(f"medications.{m}", f"active {m}"), 0.99, "medication_fact",
                                   {"generic": m, "classes": sorted(dk.classes_of(m))}))
        for c in st.active_conditions:
            claims.append(mk_claim(A, f"Condition {c.display_name} ({c.clinical_status})", ref(f"conditions.{c.code}", c.display_name), 0.99,
                                   "condition_fact", {"name": c.display_name, "status": c.clinical_status}))
        age = round(pv.age(), 1)
        claims.append(mk_claim(A, f"Age {age}, gender {st.demographics.gender}, pregnant={pv.is_pregnant()}", ref("demographics", f"age {age}"), 0.99,
                               "demographic_fact", {"age": age, "gender": st.demographics.gender, "pregnant": pv.is_pregnant()}))
        flags = []
        checks = [("metformin", "egfr", "eGFR"), ("raas_inhibitor", "potassium", "potassium"), ("hepatotoxic", "alt", "ALT"),
                  ("anticoagulant", "inr", "INR")]
        for cls_, lab, label in checks:
            if any(dk.in_class(m, cls_) or m == cls_ for m in pv.meds) and lab not in pv.labs:
                flags.append(f"Missing {label} while on {cls_}-relevant therapy (not imputed)")
        if "egfr" not in pv.labs and "creatinine" not in pv.labs:
            flags.append("No renal function measurement available (eGFR/creatinine)")
        unknown = [m for m in pv.meds if not dk.is_known(m)]
        flags += [f"Medication '{m}' is not in the knowledge base: its interactions and contraindications were NOT assessed" for m in unknown]
        flags += [f"Unit problem: {e}" for e in pv.unit_errors]
        flags = sorted(set(flags))
        return AgentOutput(agent_name=A, claims=claims, data_quality_flags=flags, confidence=max(0.4, round(1.0 - 0.15 * len(flags), 2)),
                           execution_metadata={"unknown_meds": unknown, "n_meds": len(pv.meds)},
                           narrative=f"Normalised {len(pv.labs)} labs, {len(pv.vitals)} vitals, {len(pv.meds)} medications; {len(flags)} data-quality flag(s).")
