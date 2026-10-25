"""RiskAssessmentAgent: drug-drug, drug-disease, allergy, organ-function and data-quality risk flags."""
from __future__ import annotations

from typing import List

from src.agents.base import ClinicalAgent
from src.agents.common import Facts, chunk, ckd_stage, mk_claim, stmt
from src.contracts.agent_io import AgentInput, AgentOutput
from src.contracts.recommendation import RiskFlag, RiskSeverity
from src.knowledge.drugs import get_drug_knowledge

S = RiskSeverity
_TOPIC_CHUNK = {
    "warfarin_nsaid": "EV-LABEL-WARFARIN-NSAID#0",
    "warfarin_amiodarone": "EV-LABEL-WARFARIN-AMIODARONE#0",
    "hyperkalemia_rasi": "EV-LABEL-ACEI-HYPERK#1",
    "qt_additive": "EV-CREDIBLEMEDS-QT#1",
    "statin_fibrate": "EV-LABEL-STATIN-FIBRATE#0",
    "nsaid_ssri": "EV-LABEL-NSAID-SSRI#0",
    "raas_dual": "EV-LABEL-RAAS-DUAL#0",
    "methotrexate_nsaid": "EV-LABEL-NSAID-SSRI#0",
    "metformin_contrast": "EV-LABEL-METFORMIN-CONTRAST#0",
}


class RiskAssessmentAgent(ClinicalAgent):
    ROLE = "Identify interactions, contraindications, allergy conflicts and organ-function risks"
    READS = ("medications", "allergies", "labs", "conditions", "age", "pregnancy")

    def __init__(self, **kw):
        super().__init__("risk_assessment", **kw)

    def _run(self, inp: AgentInput) -> AgentOutput:
        f = Facts.from_upstream(inp)
        dk = get_drug_knowledge()
        A = self.name
        claims, flags = [], []

        def flag(fid, sev, cat, desc, cid, kind, data, involved, advice, conf=0.9):
            c = mk_claim(A, stmt(cid) if cid else desc, [chunk(cid)] if cid else [f.ehr(inp, fid, desc)], conf, kind, data)
            claims.append(c)
            flags.append(RiskFlag(flag_id=fid, severity=sev, category=cat, description=desc, evidence=c.evidence, mitigation_advice=advice,
                                  claim_id=c.claim_id, involved=sorted(involved), source_agent=A))
        eg = f.labs.get("egfr")
        # allergies (standing awareness + active conflicts)
        for a in sorted(f.allergies):
            for key, entry in dk.allergy_map.items():
                if key in a:
                    pen = key in ("penicillin", "amoxicillin", "beta-lactam")
                    flag(f"RF-ALLERGY-{key.upper()}", S.HIGH, "allergy", f"Documented {a} allergy: avoid {'/'.join(entry['definite'])} drugs" + ("; cephalosporin cross-reactivity possible" if pen else ""),
                         "EV-JACI-2022-PENICILLIN#0" if pen else None, "allergy_conflict", {"allergy": a}, [a], "Avoid the implicated class; consider allergy evaluation")
                    break
        for a, m, lvl in f.allergy_conflicts():
            cid = "EV-JACI-2022-PENICILLIN#0" if "penicillin" in dk.classes_of(m) else None
            flag(f"RF-ALLERGY-CONFLICT-{a.upper().replace(' ', '_')}-{m.upper()}", S.CRITICAL if lvl == "definite" else S.MODERATE, "allergy",
                 f"Active {m} conflicts with documented {a} allergy ({lvl})", cid, "allergy_conflict", {"allergy": a, "drug": m, "level": lvl}, [a, m],
                 "Stop and choose a non-cross-reactive alternative" if lvl == "definite" else "Review cross-reactivity before continuing")
        # duplicate therapies
        for cls_name, in_cls in dk.duplicate_therapies(f.meds):
            flag(f"RF-DUP-{cls_name.upper()}", S.HIGH, "duplicate_therapy", f"Duplicate therapy detected in class '{cls_name}': {', '.join(in_cls)}",
                 "EV-NICE-DUPLICATE-THERAPY#0", "guideline", {"class": cls_name, "meds": in_cls}, in_cls, "Review necessity and consolidate therapy")
        # interactions
        for ix in dk.interactions(f.meds):
            chunk_id = _TOPIC_CHUNK.get(ix.topic, "EV-LABEL-WARFARIN-NSAID#0")
            flag(f"RF-DDI-{ix.drug_a.upper()}-{ix.drug_b.upper()}", S.HIGH, "drug_interaction", f"{ix.drug_a} + {ix.drug_b}: {ix.effect}", chunk_id,
                 "drug_interaction", {"drugs": sorted([ix.drug_a, ix.drug_b]), "topic": ix.topic}, [ix.drug_a, ix.drug_b], "Review combination; monitor")
        # drug-disease
        if eg is not None and "metformin" in f.meds:
            if eg < 30:
                flag("RF-METFORMIN-EGFR30", S.CRITICAL, "contraindication", f"Metformin is contraindicated at eGFR {eg} (<30)", "EV-FDA-METFORMIN#0", "contraindication",
                     {"drug": "metformin", "condition_key": "egfr<30", "patient_value": eg}, ["metformin"], "Discontinue metformin")
            elif eg < 45:
                flag("RF-METFORMIN-EGFR45", S.MODERATE, "organ_adjustment", f"Metformin dose adjustment needed at eGFR {eg} (<45)", "EV-KDIGO-DM-CKD-2022-METFORMIN#1", "contraindication",
                     {"drug": "metformin", "condition_key": "egfr<45", "patient_value": eg}, ["metformin"], "Reduce dose; monitor eGFR")
        if eg is not None and eg < 30:
            for m in f.meds_in_class("nsaid"):
                flag(f"RF-NSAID-CKD-{m.upper()}", S.HIGH, "contraindication", f"NSAID {m} at eGFR {eg} (<30)", "EV-KDIGO-CKD-2024-NSAID#1", "contraindication",
                     {"drug": m, "condition_key": "egfr<30", "patient_value": eg}, [m], "Avoid NSAID")
        if eg is not None and eg < 60:
            flag(f"RF-CKD-{ckd_stage(eg)}", S.MODERATE, "organ_adjustment", f"Reduced kidney function (GFR category {ckd_stage(eg)}): review renally cleared and nephrotoxic drugs",
                 "EV-KDIGO-CKD-2024-STAGING#0", "guideline", {"egfr": eg, "stage": ckd_stage(eg)}, [], "Renal dose review")
        k = f.labs.get("potassium")
        if k is not None and k > 5.5 and f.meds_in_class("raas_inhibitor"):
            flag("RF-HYPERK-RASI", S.HIGH, "contraindication", f"Potassium {k} (>5.5) on a RAS inhibitor", "EV-KDIGO-CKD-2024-HYPERK#1", "contraindication",
                 {"drug": "raas_inhibitor", "condition_key": "potassium>5.5", "patient_value": k}, f.meds_in_class("raas_inhibitor"), "Review RAS inhibitor; lower potassium")
        if f.pregnant:
            for m in f.meds_in_class("teratogenic"):
                cid = "EV-LABEL-ACEI-PREGNANCY#1" if dk.in_class(m, "raas_inhibitor") else "EV-LABEL-WARFARIN-PREGNANCY#0" if m == "warfarin" else None
                flag(f"RF-PREGNANCY-{m.upper()}", S.CRITICAL, "pregnancy", f"{m} in pregnancy (fetal toxicity)", cid, "contraindication",
                     {"drug": m, "condition_key": "pregnancy"}, [m], "Stop and substitute under specialist guidance", 0.95 if cid else 0.7)
        if (f.age or 0) >= 65:
            for m in f.meds:
                if dk.in_class(m, "beers_avoid") or dk.in_class(m, "beers_caution"):
                    flag(f"RF-BEERS-{m.upper()}", S.MODERATE, "beers", f"{m} is potentially inappropriate at age {f.age} (Beers)", "EV-AGS-BEERS-2023#0", "contraindication",
                         {"drug": m, "condition_key": "age>=65"}, [m], "Review need; consider deprescribing", 0.8)
        alt = f.labs.get("alt")
        if alt is not None and alt >= 120:
            for m in f.meds_in_class("hepatotoxic"):
                flag(f"RF-HEPATIC-{m.upper()}", S.HIGH, "hepatic", f"ALT {alt} (>=3xULN) with {m}", "EV-FDA-DILI-2009#1", "contraindication",
                     {"drug": m, "condition_key": "alt>3xuln", "patient_value": alt}, [m], "Repeat LFTs; consider interrupting")
        pd = inp.upstream_outputs["patient_data"]
        unknown = set(pd.execution_metadata.get("unknown_meds", []))
        for i, q in enumerate(pd.data_quality_flags):
            inv = [m for m in unknown if f"'{m}'" in q]
            flag(f"RF-DATA-{i+1}", S.MODERATE, "data_quality", q, None, "data_quality", {"note": q}, inv,
                 "Have a pharmacist/clinician assess this drug manually" if inv else "Obtain the missing data", 0.6)
        sbp_v = f.vitals.get("systolic_bp", f.vitals.get("sbp"))
        if sbp_v is None and inp.patient_state.vitals:
            v_dict = {k.lower(): x.value for k, x in inp.patient_state.vitals.items()}
            sbp_v = v_dict.get("systolic bp", v_dict.get("systolic_bp", v_dict.get("sbp")))
        if sbp_v is not None and sbp_v >= 180:
            flag("RF-VITAL-HYPERTENSION-CRISIS", S.CRITICAL, "emergency", f"Severe hypertension (systolic BP {sbp_v:g} mmHg >= 180): emergency red-flag",
                 None, "guideline", {"sbp": sbp_v}, [], "Immediate clinical evaluation / emergency referral")

        conf = round(sum(c.confidence for c in claims) / len(claims), 3) if claims else 0.9
        unc = "Uncertainty: rule-based screen over structured data only; free-text and unmodelled drugs are not assessed."
        return AgentOutput(agent_name=A, claims=claims, risk_flags=flags, confidence=conf, narrative=f"{len(flags)} risk flag(s). {unc}")

