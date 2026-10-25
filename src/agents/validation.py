"""ValidationAgent: independent adversarial verification (Part A.3 G3, C.5).

INDEPENDENCE: this agent reads the ground-truth PatientState and shared *data tables* only; it does not
import or call the Treatment/Risk decision logic. Every check recomputes from ground truth.

Severity semantics:  high   -> fact-level corruption or safety omission: withhold + escalate (unsafe_upstream)
                     medium -> a single bad claim: block it (and anything built on it) from approved output.
"""
from __future__ import annotations

from typing import Dict, List, Set

from src.agents.base import ClinicalAgent
from src.contracts.agent_io import AgentInput, AgentOutput
from src.contracts.evidence import ClinicalClaim
from src.contracts.recommendation import ValidationFinding, ValidationReport
from src.knowledge.drugs import KNOWN_CONTRAINDICATIONS, get_drug_knowledge
from src.knowledge.patient_view import PatientView
from src.provenance.claim_extractor import EvidenceLinker
from src.provenance.gate import default_resolver
from src.state.noise import noise_band
from src.units import UnitError, to_canonical

HARD = {
    ("metformin", "egfr<30"),
    ("raas_inhibitor", "pregnancy"),
    ("teratogenic", "pregnancy"),
    ("warfarin", "pregnancy"),
    ("penicillin", "allergy:penicillin"),
    ("nsaid", "egfr<30"),
    ("raas_inhibitor", "potassium>5.5"),
    ("nsaid", "interaction:anticoagulant"),
}
_LAB_OF = {"egfr<30": "egfr", "egfr<45": "egfr", "egfr<20": "egfr", "potassium>5.5": "potassium", "alt>3xuln": "alt"}


class ValidationAgent(ClinicalAgent):
    ROLE = "Adversarial cross-check of claims, facts, citations and safety completeness against ground truth"
    READS = ("ground_truth_state", "all_agent_outputs")

    def __init__(self, **kw):
        super().__init__("validation", **kw)
        self.linker = EvidenceLinker()

    # ---- helpers -------------------------------------------------------------------------
    @staticmethod
    def _holds(key: str, pv: PatientView, slack: bool = True) -> bool:
        """Does the condition hold for the record? Thresholds get a measurement-noise slack (hysteresis), so a carried-forward
        claim is not rejected merely because a value moved back across a threshold within assay variation."""
        lab = lambda n: pv.labs.get(n)
        sl = (lambda n: noise_band(n, lab(n) or 0.0)) if slack else (lambda n: 0.0)
        if key == "egfr<30": return lab("egfr") is not None and lab("egfr") < 30 + sl("egfr")
        if key == "egfr<45": return lab("egfr") is not None and lab("egfr") < 45 + sl("egfr")
        if key == "egfr<20": return lab("egfr") is not None and lab("egfr") < 20 + sl("egfr")
        if key == "potassium>5.5": return lab("potassium") is not None and lab("potassium") > 5.5 - sl("potassium")
        if key == "alt>3xuln": return lab("alt") is not None and lab("alt") >= 120 - sl("alt")
        if key == "pregnancy": return pv.is_pregnant()
        if key == "age>=65": return pv.age() >= 65
        if key == "allergy:penicillin": return any("penicillin" in a or "amoxicillin" in a or "beta-lactam" in a for a in pv.allergies)
        if key == "interaction:anticoagulant": return bool(pv.meds_in_class("anticoagulant"))
        return False

    def _drug_applies(self, drug: str, pv: PatientView) -> bool:
        return drug in pv.meds or bool(pv.meds_in_class(drug))

    def _run(self, inp: AgentInput) -> AgentOutput:
        st, outs = inp.patient_state, inp.upstream_outputs
        pv, dk = PatientView(st), get_drug_knowledge()
        F: List[ValidationFinding] = []
        blocked: Set[str] = set()
        checks = 0

        def find(check, detail, sev, claim=None, agent=None):
            F.append(ValidationFinding(check=check, detail=detail, severity=sev, claim_id=claim.claim_id if claim else None, agent=agent or (claim.agent_source if claim else None)))
            if claim is not None:
                blocked.add(claim.claim_id)

        # 1. fact fidelity of the PatientDataAgent output ---------------------------------
        pd = outs.get("patient_data")
        if pd is not None:
            seen: Dict[str, Set[str]] = {"lab": set(), "allergy": set(), "med": set(), "cond": set()}
            for c in pd.claims:
                if not c.structured:
                    continue
                k, d = c.structured.kind, c.structured.data
                checks += 1
                if k in ("lab_value", "vital_value"):
                    truth = (pv.labs if k == "lab_value" else pv.vitals).get(d["analyte"])
                    seen["lab"].add(d["analyte"])
                    try:
                        claimed = to_canonical(d["analyte"], d["value"], d["unit"])
                    except UnitError:
                        claimed = None
                    if truth is None or claimed is None or abs(claimed - truth) > 1e-3 * max(1.0, abs(truth)):
                        find("fact_mismatch", f"{d['analyte']}: claimed {d['value']} {d['unit']} but the record says {truth}", "high", c, "patient_data")
                elif k == "allergy_fact":
                    seen["allergy"].add(d["substance"])
                    if d["substance"] not in pv.allergies:
                        find("fact_mismatch", f"allergy '{d['substance']}' is not in the record", "high", c, "patient_data")
                elif k == "medication_fact":
                    seen["med"].add(d["generic"])
                    if d["generic"] not in pv.meds:
                        find("fact_mismatch", f"medication '{d['generic']}' is not active in the record", "high", c, "patient_data")
                elif k == "demographic_fact":
                    if abs(d["age"] - pv.age()) > 0.2 or d["pregnant"] != pv.is_pregnant():
                        find("fact_mismatch", "age/pregnancy status differs from the record", "high", c, "patient_data")
            checks += 4
            for a in set(pv.labs) - seen["lab"]:
                find("fact_omitted", f"lab '{a}' is in the record but missing from normalised facts", "high", None, "patient_data")
            for a in set(pv.allergies) - seen["allergy"]:
                find("fact_omitted", f"allergy '{a}' is in the record but was dropped", "high", None, "patient_data")
            for m in set(pv.meds) - seen["med"]:
                find("fact_omitted", f"medication '{m}' is in the record but was dropped", "high", None, "patient_data")
        # 2. trajectory end-points --------------------------------------------------------
        eh = outs.get("ehr_analysis")
        if eh is not None:
            for c in eh.claims:
                if c.structured and c.structured.kind == "trajectory" and c.structured.data.get("analyte"):
                    checks += 1
                    t = pv.labs.get(c.structured.data["analyte"])
                    if t is None or abs(c.structured.data["current"] - t) > 1e-3 * max(1.0, abs(t)):
                        find("fact_mismatch", f"trajectory end-point for {c.structured.data['analyte']} differs from the record", "high", c, "ehr_analysis")
        # 3+4. claim-level structured + evidence checks -----------------------------------
        all_claims: List[ClinicalClaim] = [c for n, o in outs.items() if n != "validation" for c in o.claims]
        unsupported: List[str] = []
        for c in all_claims:
            checks += 1
            if not c.evidence or not all(default_resolver(e) for e in c.evidence):
                unsupported.append(c.claim_id)
                find("unresolvable_evidence", "claim has no evidence or cites an id that does not resolve", "medium", c)
                continue
            s = c.structured
            if not s:
                continue
            k, d = s.kind, s.data
            if k == "drug_interaction":
                a, b = d["drugs"]
                if not (self._drug_applies(a, pv) and self._drug_applies(b, pv)):
                    find("interaction_not_applicable", f"interaction {a}+{b}: patient is not taking both drugs", "medium", c)
                elif not dk.is_known_interaction(a, b):
                    find("interaction_unknown", f"{a}+{b} is not in the interaction table", "medium", c)
            elif k == "contraindication":
                drug, key = d["drug"], d["condition_key"]
                known = (drug, key) in KNOWN_CONTRAINDICATIONS or any((cl, key) in KNOWN_CONTRAINDICATIONS for cl in dk.classes_of(drug))
                if not known:
                    find("contraindication_unknown", f"'{drug}' / '{key}' is not a known contraindication", "medium", c)
                elif not self._drug_applies(drug, pv) or not self._holds(key, pv):
                    find("contraindication_not_applicable", f"'{drug}' / '{key}' does not apply to this patient's record", "medium", c)
                elif "patient_value" in d and key in _LAB_OF and d["patient_value"] is not None:
                    t = pv.labs.get(_LAB_OF[key])
                    if t is None or abs(d["patient_value"] - t) > 1.01 * noise_band(_LAB_OF[key], t):  # tolerate assay noise on carried claims
                        find("fact_mismatch", f"claim value {d['patient_value']} differs from the record value {t}", "high", c)
            elif k == "allergy_conflict":
                al = d.get("allergy", "")
                if al not in pv.allergies:
                    find("allergy_not_documented", f"allergy '{al}' is not documented", "high", c)
                elif d.get("drug") and (d["drug"] not in pv.meds or not dk.allergy_conflict(al, d["drug"])):
                    find("allergy_conflict_invalid", f"'{al}' vs '{d['drug']}' is not a conflict for this record", "medium", c)
                elif d.get("level") == "definite" and dk.allergy_conflict(al, d.get("drug", "")) != "definite":
                    find("allergy_conflict_invalid", f"'{al}' vs '{d.get('drug')}' is not a definite conflict (level is {dk.allergy_conflict(al, d.get('drug'))})", "medium", c)
            if k in ("guideline", "drug_interaction", "contraindication", "allergy_conflict", "differential"):
                ext = [e for e in c.evidence if e.source.value != "local_ehr"]
                if ext and not any(self.linker.entails(c.assertion, e.snippet) for e in ext):
                    find("evidence_not_entailing", "cited evidence does not support the assertion", "medium", c)
        # 5. omission of safety-critical flags (independent recomputation) ------------------
        ra = outs.get("risk_assessment")
        if ra is not None:
            flags = ra.risk_flags
            def has(cat, *inv):
                return any(f.category == cat and all(i in f.involved or any(i in x for x in f.involved) for i in inv) for f in flags)
            checks += 1
            for a, m, lvl in pv.allergy_conflicts():
                if lvl == "definite" and not has("allergy", m):
                    find("omitted_risk_flag", f"allergy conflict {a} vs {m} was not flagged", "high", None, "risk_assessment")
            for a in pv.allergies:
                if any(key in a for key in dk.allergy_map) and not has("allergy", a):
                    find("omitted_risk_flag", f"documented allergy '{a}' has no allergy flag", "high", None, "risk_assessment")
            for ix in dk.interactions(pv.meds):
                if not has("drug_interaction", ix.drug_a, ix.drug_b):
                    find("omitted_risk_flag", f"interaction {ix.drug_a}+{ix.drug_b} was not flagged", "high", None, "risk_assessment")
            if pv.labs.get("egfr") is not None and pv.labs["egfr"] < 30 and "metformin" in pv.meds and not has("contraindication", "metformin"):
                find("omitted_risk_flag", "metformin at eGFR<30 was not flagged", "high", None, "risk_assessment")
            if pv.is_pregnant():
                for m in pv.meds_in_class("teratogenic"):
                    if not has("pregnancy", m):
                        find("omitted_risk_flag", f"teratogenic {m} in pregnancy was not flagged", "high", None, "risk_assessment")
            sbp_val = pv.vitals.get("systolic_bp", pv.vitals.get("sbp"))
            if sbp_val is not None and sbp_val >= 180:
                if not any("hypertens" in f.description.lower() or "blood pressure" in f.description.lower() or "sbp" in f.description.lower() for f in flags):
                    find("omitted_risk_flag", f"severe hypertension (systolic BP {sbp_val:g} mmHg >= 180) was not flagged as emergency red-flag", "high", None, "risk_assessment")
        # 6. recommended actions vs hard contraindications ------------------------------------
        tr = outs.get("treatment")
        if tr is not None:
            sbp_val = pv.vitals.get("systolic_bp", pv.vitals.get("sbp"))
            for a in tr.actions:
                checks += 1
                g = dk.normalise(a.target)
                if a.action_type in ("prescribe", "continue", "adjust_dose", "monitor"):
                    conds = {cond for (_, cond) in HARD}
                    for cond in conds:
                        names = {g} | dk.classes_of(g)
                        if any((n, cond) in HARD for n in names) and self._holds(cond, pv, slack=False):   # strict: a FRESH action must never contradict a hard contraindication
                            find("guideline_conflict", f"action '{a.action_type} {a.target}' conflicts with a hard contraindication ({cond})", "high", None, "treatment")
                    if g == "metformin" and "egfr" not in pv.labs:
                        find("guideline_conflict", f"action '{a.action_type} metformin' requires eGFR measurement which is missing from record", "high", None, "treatment")
                    if (
                        not dk.is_known(g)
                        and not any(w in g for w in ("regimen", "therapy", "current"))
                        and a.action_type in ("prescribe", "continue", "adjust_dose")
                    ):
                        find("unsupported_medication", f"action '{a.action_type} {a.target}' involves an unknown/unsupported drug not in knowledge base", "high", None, "treatment")

                if sbp_val is not None and sbp_val >= 180:
                    if a.urgency == "routine" or "routine" in a.details.lower() or "no urgent concern" in a.details.lower():
                        find("guideline_conflict", f"action '{a.action_type} {a.target}' with routine urgency conflicts with emergency hypertensive crisis (SBP {sbp_val:g} mmHg)", "high", None, "treatment")

        high = [f for f in F if f.severity == "high"]
        contradictions = [f.detail for f in F if f.check in ("guideline_conflict",)]
        report = ValidationReport(
            report_id=f"VAL-{st.patient_id}-v{st.version}", passed=not F, fault_detected=bool(F),
            containment_status="clean" if not F else "contained", findings=F, blocked_claim_ids=sorted(blocked),
            unsupported_claims=sorted(set(unsupported)), unsafe_upstream=bool(high), checks_run=checks,
            guideline_conflicts=contradictions, internal_contradictions=[f.detail for f in F if f.check.endswith("mismatch")])
        return AgentOutput(agent_name=self.name, validation_report=report, confidence=max(0.0, 1.0 - 0.25 * len(high) - 0.1 * (len(F) - len(high))),
                           narrative=f"{checks} checks; {len(F)} finding(s) ({len(high)} high).")


