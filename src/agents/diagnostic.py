"""DiagnosticAgent: ranked differential (never a single diagnosis) with supporting/refuting findings."""
from __future__ import annotations

from src.agents.base import ClinicalAgent
from src.agents.common import Facts, chunk, ckd_stage, mk_claim, stmt
from src.contracts.agent_io import AgentInput, AgentOutput, DifferentialItem
from src.knowledge.patient_view import PatientView


class DiagnosticAgent(ClinicalAgent):
    ROLE = "Generate a ranked differential from findings; propose confirmatory tests"
    READS = ("labs", "vitals", "conditions", "age")

    def __init__(self, **kw):
        super().__init__("diagnostic", **kw)

    def _run(self, inp: AgentInput) -> AgentOutput:
        f = Facts.from_upstream(inp)
        A = self.name
        prev = PatientView(inp.previous_state) if inp.previous_state is not None else None
        items, claims = [], []
        if not f.labs and not f.vitals:
            return AgentOutput(agent_name=A, abstained=True, abstention_reason="No objective data (labs/vitals) available to form a differential", confidence=0.0)

        def add(cond, rank, sup, ref, note, txt, data):
            items.append(DifferentialItem(condition=cond, rank=rank, supporting=sup, refuting=[], note=note))
            claims.append(mk_claim(A, txt, [ref] if ref else [], 0.8, "differential", {"condition": cond, **data}))
        rank = 1
        eg = f.labs.get("egfr")
        if eg is not None and eg < 60:
            pe = prev.labs.get("egfr") if prev else None
            aki = pe is not None and pe > 0 and (pe - eg) / pe >= 0.30
            if aki:
                add("Acute kidney injury or rapid CKD progression", rank, [f"eGFR fell {pe} -> {eg}"], chunk("EV-KDIGO-AKI-2012#0"),
                    "Confirm with repeat creatinine; review nephrotoxic drugs", stmt("EV-KDIGO-AKI-2012#0"), {"egfr": eg, "prev_egfr": pe})
                rank += 1
            add(f"Chronic kidney disease, GFR category {ckd_stage(eg)}", rank, [f"eGFR {eg}"], chunk("EV-KDIGO-CKD-2024-STAGING#0"),
                "Chronicity needs >=3 months of reduced eGFR; single value cannot confirm", stmt("EV-KDIGO-CKD-2024-STAGING#0"), {"egfr": eg, "stage": ckd_stage(eg)})
            rank += 1
            if f.meds_in_class("nsaid") or f.meds_in_class("raas_inhibitor"):
                add("Medication-related reduction in GFR (NSAID / RAS inhibitor)", rank, ["NSAID or RAS inhibitor on medication list"], chunk("EV-KDIGO-CKD-2024-NSAID#0"),
                    "Review nephrotoxic/haemodynamic drugs", stmt("EV-KDIGO-CKD-2024-NSAID#0"), {"egfr": eg})
                rank += 1
        k = f.labs.get("potassium")
        if k is not None and k > 5.2:
            add("Hyperkalaemia related to RAS inhibitor and/or reduced renal potassium excretion", rank, [f"potassium {k}"], chunk("EV-LABEL-ACEI-HYPERK#0"),
                "Repeat sample (exclude haemolysis); ECG if marked", stmt("EV-LABEL-ACEI-HYPERK#0"), {"potassium": k})
            rank += 1
        alt = f.labs.get("alt")
        if alt is not None and alt >= 120:
            add("Drug-induced liver injury", rank, [f"ALT {alt} (>=3xULN=120)"], chunk("EV-FDA-DILI-2009#0"),
                "Repeat LFTs; review hepatotoxic drugs", stmt("EV-FDA-DILI-2009#0"), {"alt": alt})
            rank += 1
        a1c = f.labs.get("hba1c")
        if a1c is not None and a1c >= 7.0 and f.has_condition("diabetes"):
            add("Suboptimal glycaemic control in diabetes", rank, [f"HbA1c {a1c}%"], chunk("EV-ADA-2024-TARGETS#0"),
                "Assess adherence, hypoglycaemia risk, regimen", stmt("EV-ADA-2024-TARGETS#0"), {"hba1c": a1c})
            rank += 1
        if not items:
            txt = "No abnormal laboratory findings requiring a differential were identified in the available data"
            from src.agents.common import ehr_ref
            claims.append(mk_claim(A, txt, [ehr_ref(inp.patient_state.patient_id, inp.patient_state.version, "labs", "evaluated labs: " + ",".join(sorted(f.labs)))], 0.8, "data_quality", {}))
        return AgentOutput(agent_name=A, claims=claims, differential=items, confidence=0.8,
                           narrative="; ".join(f"{i.rank}. {i.condition}" for i in items) or "No differential required.")
