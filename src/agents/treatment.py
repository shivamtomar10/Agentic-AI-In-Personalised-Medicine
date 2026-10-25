"""TreatmentAgent: personalised, evidence-linked treatment options for clinician review.

Every action references >=1 claim (Rule 4 applies to actions). Rule-based decision core: the
thresholds are guideline/label statements encoded from the curated corpus, not model memory.
"""
from __future__ import annotations

from typing import List

from src.agents.base import ClinicalAgent
from src.agents.common import Facts, chunk, mk_claim, stmt
from src.contracts.agent_io import AgentInput, AgentOutput
from src.contracts.evidence import ClinicalClaim
from src.contracts.recommendation import ClinicalAction, DraftPrescription
from src.knowledge.drugs import get_drug_knowledge
import uuid

_URG = {"urgent": 0, "high": 1, "routine": 2}


class TreatmentAgent(ClinicalAgent):
    ROLE = "Propose personalised treatment options with pros/cons, evidence-linked"
    READS = ("labs", "vitals", "medications", "allergies", "conditions", "age", "pregnancy")

    def __init__(self, **kw):
        super().__init__("treatment", **kw)

    @staticmethod
    def _merge(actions: List[ClinicalAction]) -> List[ClinicalAction]:
        """One action per target among stop/avoid/review; strongest verb wins, evidence and reasons are unioned."""
        rank = {"discontinue": 0, "avoid": 1, "review": 2}
        out: List[ClinicalAction] = []
        by_target = {}
        for a in actions:
            if a.action_type not in rank:
                out.append(a)
                continue
            cur = by_target.get(a.target)
            if cur is None:
                by_target[a.target] = a.model_copy(deep=True)
                continue
            win, lose = (a, cur) if rank[a.action_type] < rank[cur.action_type] else (cur, a)
            m = win.model_copy(deep=True)
            m.claim_ids = list(dict.fromkeys(win.claim_ids + lose.claim_ids))
            if lose.rationale not in m.rationale:
                m.rationale = f"{win.rationale}; also: {lose.rationale}"
            if _URG.get(lose.urgency, 3) < _URG.get(m.urgency, 3):
                m.urgency = lose.urgency
            m.options = list(dict.fromkeys(win.options + lose.options))
            by_target[a.target] = m
        return out + list(by_target.values())

    def _run(self, inp: AgentInput) -> AgentOutput:
        f = Facts.from_upstream(inp)
        dk = get_drug_knowledge()
        A = self.name
        claims: List[ClinicalClaim] = []
        actions: List[ClinicalAction] = []

        def act(kind, target, details, rationale, cids, urgency="routine", options=None):
            actions.append(ClinicalAction(action_type=kind, target=target, details=details, rationale=rationale, urgency=urgency,
                                          source_agent=A, claim_ids=cids, options=options or []))

        def basis(cid, kind, data, conf=0.85):
            c = mk_claim(A, stmt(cid), [chunk(cid)], conf, kind, data)
            claims.append(c)
            return c.claim_id

        eg = f.labs.get("egfr")
        metformin_stopped = False
        # --- metformin renal safety --------------------------------------------------
        if "metformin" in f.meds:
            if eg is None:
                c = mk_claim(A, "eGFR is not available for a patient taking metformin; renal function cannot be assessed", [f.ehr(inp, "labs.egfr", "eGFR missing")], 0.5,
                             "data_quality", {"missing": "egfr"})
                claims.append(c)
                act("order_lab", "eGFR", "Obtain eGFR before continuing metformin (value not imputed)", "Renal function determines metformin safety", [c.claim_id], "high")
            elif eg < 30:
                cid = basis("EV-FDA-METFORMIN#3", "contraindication", {"drug": "metformin", "condition_key": "egfr<30", "patient_value": eg}, 0.95)
                act("discontinue", "metformin", f"eGFR {eg} mL/min/1.73m2 is below 30: stop metformin", "Metformin is contraindicated below eGFR 30 (FDA labelling; KDIGO 2022)", [cid], "urgent",
                    ["Pro: removes lactic acidosis risk", "Con: loses a glucose-lowering agent - see alternatives"])
                metformin_stopped = True
            elif eg < 45:
                cid = basis("EV-KDIGO-DM-CKD-2022-METFORMIN#1", "contraindication", {"drug": "metformin", "condition_key": "egfr<45", "patient_value": eg}, 0.9)
                act("adjust_dose", "metformin", f"eGFR {eg} is 30-44: reduce dose (max 1000 mg/day); do not initiate", "Dose reduction below eGFR 45", [cid], "high",
                    ["Pro: keeps an effective, cheap agent", "Con: needs eGFR monitoring every 3 months"])
        # --- glycaemic control ---------------------------------------------------------
        a1c = f.labs.get("hba1c")
        if a1c is not None:
            goal = 8.0 if (f.age or 0) >= 65 or (eg is not None and eg < 30) else 7.0
            if a1c > goal:
                c1 = basis("EV-ADA-2024-TARGETS#0", "guideline", {"hba1c": a1c, "goal": goal})
                c2 = basis("EV-ADA-2024-INTENSIFY#0", "guideline", {"hba1c": a1c, "goal": goal})
                act("review", "glucose-lowering regimen", f"HbA1c {a1c}% exceeds the individual goal {goal}%: review and intensify therapy", "Avoid therapeutic inertia", [c1, c2],
                    "high" if a1c >= 9 else "routine")
        # --- diabetes + CKD options ----------------------------------------------------
        if f.has_condition("diabetes") and eg is not None and eg < 60:
            opts, cids = [], []
            if not f.meds_in_class("sglt2_inhibitor") and eg >= 20:
                cids.append(basis("EV-KDIGO-DM-CKD-2022-SGLT2#0", "guideline", {"egfr": eg}))
                opts.append("SGLT2 inhibitor - Pro: kidney/cardiac protection; Con: reduced glucose lowering at low eGFR, genital infections, volume depletion")
            if not f.meds_in_class("glp1_ra"):
                cids.append(basis("EV-KDIGO-DM-CKD-2022-GLP1#0", "guideline", {"egfr": eg}))
                opts.append("Long-acting GLP-1 receptor agonist - Pro: glucose and weight benefit, no renal adjustment; Con: GI effects, cost")
            if metformin_stopped:
                cids.append(basis("EV-LABEL-DPP4-RENAL#1", "guideline", {"egfr": eg}))
                opts.append("DPP-4 inhibitor (linagliptin needs no renal adjustment) - Pro: low hypoglycaemia risk; Con: modest efficacy")
                cids.append(basis("EV-KDIGO-DM-CKD-2022-INSULIN#0", "guideline", {"egfr": eg}))
                opts.append("Insulin - Pro: effective at any eGFR; Con: hypoglycaemia risk rises as kidney function falls")
            if opts:
                act("review", "diabetes therapy in CKD", "Options to consider for a patient with type 2 diabetes and reduced eGFR", "KDIGO 2022 diabetes-in-CKD guidance", cids, "routine", opts)
        # --- potassium ----------------------------------------------------------------
        k = f.labs.get("potassium")
        rasi = f.meds_in_class("raas_inhibitor")
        if k is not None and rasi and k > 5.5:
            cid = basis("EV-KDIGO-CKD-2024-HYPERK#1", "contraindication", {"drug": "raas_inhibitor", "condition_key": "potassium>5.5", "patient_value": k}, 0.9)
            act("review", ", ".join(rasi), f"Potassium {k} mmol/L on a RAS inhibitor: review dose/hold and use potassium-lowering measures; repeat potassium", "Hyperkalaemia on RAS inhibitor", [cid], "urgent",
                ["Pro: lowers hyperkalaemia and arrhythmia risk", "Con: loses renal/cardiac protection if stopped long-term"])
        elif k is not None and 5.2 < k <= 5.5:
            cid = basis("EV-LABEL-ACEI-HYPERK#2", "guideline", {"potassium": k})
            act("order_lab", "potassium", f"Potassium {k} is mildly elevated: repeat and monitor", "Monitor potassium", [cid], "routine")
        # --- allergies -----------------------------------------------------------------
        for a in sorted(f.allergies):
            for key, entry in dk.allergy_map.items():
                if key in a:
                    if key in ("penicillin", "amoxicillin", "beta-lactam"):
                        c1 = basis("EV-JACI-2022-PENICILLIN#0", "allergy_conflict", {"allergy": a})
                        c2 = basis("EV-JACI-2022-PENICILLIN#1", "guideline", {"allergy": a})
                        act("avoid", "penicillin-class antibiotics", f"Documented {a} allergy: avoid penicillins; use caution with cephalosporins pending allergy evaluation", "Beta-lactam avoidance", [c1, c2], "high")
                    else:
                        cls_txt = "/".join(entry["definite"])
                        c = mk_claim(A, f"Documented {a} allergy: avoid {cls_txt} drugs", [f.ehr(inp, f"allergies.{a}", f"allergy {a}")], 0.9, "allergy_conflict", {"allergy": a})
                        claims.append(c)
                        act("avoid", f"{cls_txt} drugs", f"Documented {a} allergy: avoid {cls_txt} drugs", "Allergy avoidance", [c.claim_id], "high")
                    break
        for a, m, lvl in f.allergy_conflicts():
            if lvl == "possible":   # e.g. cephalosporin in a penicillin-allergic patient: cross-reactivity is low but real -> review, not silence
                if any(k in a for k in ("penicillin", "amoxicillin", "beta-lactam")):
                    cid = basis("EV-JACI-2022-PENICILLIN#1", "allergy_conflict", {"allergy": a, "drug": m, "level": lvl}, 0.8)
                else:
                    c = mk_claim(A, f"Documented allergy {a} may cross-react with active {m}", [f.ehr(inp, f"allergies.{a}", f"allergy {a}; active {m}")], 0.7, "allergy_conflict", {"allergy": a, "drug": m, "level": lvl})
                    claims.append(c)
                    cid = c.claim_id
                act("review", m, f"Documented {a} allergy with active {m}: possible cross-reactivity; confirm the reaction history and side-chain similarity before continuing", "Possible cross-reactivity", [cid], "high",
                    ["Pro: cephalosporins with dissimilar side chains are often usable", "Con: risk is not zero; consider allergy evaluation"])
            if lvl == "definite":
                cid = basis("EV-JACI-2022-PENICILLIN#0", "allergy_conflict", {"allergy": a, "drug": m, "level": lvl}) if "penicillin" in dk.classes_of(m) else None
                if cid is None:
                    c = mk_claim(A, f"Documented allergy {a} conflicts with active {m}", [f.ehr(inp, f"allergies.{a}", f"allergy {a}; active {m}")], 0.9, "allergy_conflict", {"allergy": a, "drug": m, "level": lvl})
                    claims.append(c)
                    cid = c.claim_id
                act("discontinue", m, f"Active {m} conflicts with documented {a} allergy: stop and choose an alternative", "Allergy conflict", [cid], "urgent")
        # --- duplicate therapies ---------------------------------------------------------
        for cls_name, in_cls in dk.duplicate_therapies(f.meds):
            cid = basis("EV-NICE-DUPLICATE-THERAPY#0", "guideline", {"class": cls_name, "meds": in_cls}, 0.85)
            act("review", f"{cls_name} class duplication ({', '.join(in_cls)})", f"Multiple medications prescribed in class '{cls_name}': review necessity and simplify regimen", "Duplicate therapy risk", [cid], "high")

        # --- interactions ----------------------------------------------------------------
        topic_chunk = {
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
        for ix in dk.interactions(f.meds):
            chunk_id = topic_chunk.get(ix.topic, "EV-LABEL-WARFARIN-NSAID#0")
            cid = basis(chunk_id, "drug_interaction", {"drugs": sorted([ix.drug_a, ix.drug_b]), "topic": ix.topic}, 0.9)
            if ix.topic == "warfarin_nsaid":
                nsaid = ix.drug_a if dk.in_class(ix.drug_a, "nsaid") else ix.drug_b
                act("discontinue", nsaid, f"{nsaid} with an anticoagulant raises bleeding risk: stop/avoid, use non-NSAID analgesia, monitor INR/bleeding", "Bleeding risk", [cid], "high")
            elif ix.topic == "qt_additive":
                act("review", f"{ix.drug_a} + {ix.drug_b}", "Two QT-prolonging drugs: obtain ECG and electrolytes; consider an alternative to one", "Additive QT prolongation", [cid], "high")
            elif ix.topic == "statin_fibrate":
                act("review", f"{ix.drug_a} + {ix.drug_b}", "Statin + fibrate combination: severe myopathy/rhabdomyolysis risk; monitor CPK and consider alternative", "Myopathy risk", [cid], "high")
            elif ix.topic == "raas_dual":
                act("discontinue", ix.drug_b, f"Dual RAAS blockade ({ix.drug_a} + {ix.drug_b}): discontinue one agent due to hyperkalemia and renal risks", "Dual RAAS warning", [cid], "urgent")
            else:
                act("review", f"{ix.drug_a} + {ix.drug_b}", f"{ix.effect}: review the combination and monitor", ix.effect, [cid], "high")
        # --- NSAID with advanced CKD ---------------------------------------------------
        if eg is not None and eg < 30:
            for m in f.meds_in_class("nsaid"):
                cid = basis("EV-KDIGO-CKD-2024-NSAID#1", "contraindication", {"drug": m, "condition_key": "egfr<30", "patient_value": eg})
                act("avoid", m, f"eGFR {eg}: avoid NSAID {m}", "Nephrotoxicity in advanced CKD", [cid], "high")
        # --- pregnancy -----------------------------------------------------------------
        if f.pregnant:
            for m in f.meds_in_class("teratogenic"):
                cids = []
                if dk.in_class(m, "raas_inhibitor"):
                    cids.append(basis("EV-LABEL-ACEI-PREGNANCY#1", "contraindication", {"drug": m, "condition_key": "pregnancy"}, 0.95))
                    cids.append(basis("EV-ACOG-PB222-HTN-PREG#0", "guideline", {}))
                elif m == "warfarin":
                    cids.append(basis("EV-LABEL-WARFARIN-PREGNANCY#0", "contraindication", {"drug": m, "condition_key": "pregnancy"}, 0.95))
                else:
                    c = mk_claim(A, f"{m} is a recognised teratogen and pregnancy is documented", [f.ehr(inp, "conditions.pregnancy", "pregnancy documented")], 0.7, "contraindication", {"drug": m, "condition_key": "pregnancy"})
                    claims.append(c)
                    cids.append(c.claim_id)
                act("discontinue", m, f"Pregnancy documented: stop {m} and substitute a pregnancy-compatible alternative under specialist guidance", "Fetal toxicity", cids, "urgent",
                    ["Pro: avoids fetal harm", "Con: needs specialist input to select the alternative"])
        # --- Beers -----------------------------------------------------------------------
        if (f.age or 0) >= 65:
            for m in f.meds:
                if dk.in_class(m, "beers_avoid") or dk.in_class(m, "beers_caution"):
                    cid = basis("EV-AGS-BEERS-2023#0", "contraindication", {"drug": m, "condition_key": "age>=65", "patient_value": f.age}, 0.8)
                    act("review", m, f"Age {f.age}: {m} is on the Beers list; review need and consider deprescribing", "Potentially inappropriate medication in older adults", [cid], "routine")
        # --- hepatic ---------------------------------------------------------------------
        alt = f.labs.get("alt")
        if alt is not None and alt >= 120:
            for m in f.meds_in_class("hepatotoxic"):
                cid = basis("EV-FDA-DILI-2009#1", "contraindication", {"drug": m, "condition_key": "alt>3xuln", "patient_value": alt}, 0.85)
                act("review", m, f"ALT {alt} U/L is >=3xULN on {m}: repeat LFTs and consider interrupting", "Possible drug-induced liver injury", [cid], "high")
        # --- resolved conditions -> therapy review ---------------------------------------
        links = {"hypertension": ("raas_inhibitor", "ccb"), "deep vein thrombosis": ("anticoagulant",), "pulmonary embolism": ("anticoagulant",), "venous thromboembolism": ("anticoagulant",)}
        for rc in sorted(f.resolved_conditions):
            for kw, classes in links.items():
                if kw in rc.lower():
                    for m in f.meds:
                        if any(dk.in_class(m, c) for c in classes):
                            cid = basis("EV-NICE-NG5-REVIEW#1", "guideline", {"resolved": rc, "drug": m})
                            act("review", m, f"'{rc}' is documented as resolved: review whether {m} is still indicated", "Indication may have resolved", [cid], "routine")
        # --- blood pressure --------------------------------------------------------------
        sbp = f.vitals.get("systolic_bp")
        if sbp is not None:
            if sbp >= 180:
                cid = basis("EV-ADA-2024-BP#0", "guideline", {"sbp": sbp})
                act("review", "blood pressure regimen", f"Systolic BP {sbp} mmHg is severely elevated (hypertensive emergency/crisis): urgent clinical review required", "Severely elevated blood pressure", [cid], "urgent")
            elif sbp >= 130 and (f.has_condition("diabetes") or (eg is not None and eg < 60) or f.has_condition("hypertension")):
                cids = [basis("EV-ADA-2024-BP#0", "guideline", {"sbp": sbp})]
                if eg is not None and eg < 60:
                    cids.append(basis("EV-KDIGO-BP-2021#0", "guideline", {"sbp": sbp}))
                act("review", "blood pressure regimen", f"Systolic BP {sbp} mmHg is above target (<130/80 ADA; SBP <120 KDIGO when tolerated): review antihypertensive therapy", "BP above target", cids, "routine")

        # --- nothing triggered -----------------------------------------------------------
        if not actions:
            c = mk_claim(A, "No treatment-modifying rule was triggered by the evaluated data", [f.ehr(inp, "rules", f"evaluated: labs={sorted(f.labs)} meds={f.meds}")], 0.8, "action_basis", {})
            claims.append(c)
            act("monitor", "current regimen", "Continue current regimen with routine monitoring", "No rule triggered", [c.claim_id])
        claims = list({c.claim_id: c for c in claims}.values())
        actions = self._merge(actions)
        actions.sort(key=lambda a: (_URG.get(a.urgency, 3), a.target, a.action_type))
        
        # Generate draft prescriptions for active medications needing clinician verification
        draft_rx = []
        for m in f.meds:
            rx_id = f"RX-DRAFT-{uuid.uuid4().hex[:6].upper()}"
            draft_rx.append(
                DraftPrescription(
                    prescription_id=rx_id,
                    medication=m,
                    dosage="As recorded",
                    route="oral",
                    frequency="daily",
                    duration="90 days",
                    sig=f"Take {m} as directed by prescribing clinician",
                    status="draft",
                    safety_warnings=[
                        a.details
                        for a in actions
                        if a.target == m and a.action_type in ("discontinue", "avoid", "adjust_dose")
                    ],
                )
            )

        conf = round(sum(c.confidence for c in claims) / len(claims), 3) if claims else 0.8
        return AgentOutput(
            agent_name=A,
            claims=claims,
            actions=actions,
            draft_prescriptions=draft_rx,
            confidence=conf,
            narrative=f"{len(actions)} action(s) proposed for clinician review; {len(draft_rx)} draft prescription(s) generated.",
        )
