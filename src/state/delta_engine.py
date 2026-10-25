"""DeltaEngine: rule-driven, auditable detection of clinically significant change.
Implements Temporal Personalization, Part A.2 & C.3 (the core research contribution).

Significance comes from config/significance_rules.yaml (editable, no code change). Design choices:
  * unit normalisation first, so mg/dL vs umol/L never yields a false delta;
  * changes inside an analytical/biological noise band are NOISE (sig 0.0);
  * ASYMMETRIC HYSTERESIS: crossing a decision threshold in the UNSAFE direction is never
    suppressed as noise (be quick to flag harm); crossing back to the safe side inside the noise band
    is suppressed (be slow to relax) - this prevents threshold flip-flopping;
  * analytes with no rule are classified by a fail-safe default (0.35 -> re-plan), flagged
    classifier="default" so rule-vs-default coverage can be reported. (The optional LLM pass of the
    proposal is NOT implemented.)
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

import yaml

from src.config import PROJECT_ROOT, get_settings
from src.contracts.delta import Delta, DeltaCategory, DeltaReport
from src.contracts.patient import PatientState
from src.knowledge.drugs import get_drug_knowledge
from src.knowledge.patient_view import PatientView

C = DeltaCategory


def _norm_allergy(s: str) -> str:
    return re.sub(r"\(.*?\)", "", s).strip().lower()


def _norm_dose(s: str) -> str:
    return re.sub(r"\s+", "", (s or "").lower())


class DeltaEngine:
    def __init__(self, rules_path: Optional[str] = None, agent_map_path: Optional[str] = None, threshold: Optional[float] = None):
        cfg = get_settings().config_dir
        self.rules_path = Path(rules_path) if rules_path else cfg / "significance_rules.yaml"
        self.agent_map_path = Path(agent_map_path) if agent_map_path else cfg / "delta_agent_map.yaml"
        self.rules: Dict[str, Any] = yaml.safe_load(self.rules_path.read_text(encoding="utf-8"))
        self.agent_map: Dict[str, Any] = yaml.safe_load(self.agent_map_path.read_text(encoding="utf-8"))
        self.threshold = threshold if threshold is not None else get_settings().replan_threshold
        self.dk = get_drug_knowledge()

    # ------------------------------------------------------------------ public
    def compute(self, prev: Optional[PatientState], curr: PatientState) -> DeltaReport:
        deltas: List[Delta] = []
        if prev is not None:
            pv, cv = PatientView(prev), PatientView(curr)
            deltas += self._labs(pv, cv)
            deltas += self._vitals(pv, cv)
            deltas += self._conditions(pv, cv)
            deltas += self._meds(prev, curr)
            deltas += self._allergies(prev, curr)
            deltas += self._demographics(pv, cv)
            deltas += self._other(prev, curr)
        return DeltaReport(
            patient_id=curr.patient_id,
            previous_version=prev.version if prev else None,
            current_version=curr.version,
            deltas=deltas,
            threshold=self.threshold,
            unclassified_count=sum(1 for d in deltas if d.classifier == "default"),
        )

    def affected_agents(self, report: DeltaReport) -> Set[str]:
        sig = [d for d in report.deltas if d.significance >= self.threshold]
        if not sig:
            return set()
        out: Set[str] = set(self.agent_map.get("context_agents", []))
        for d in sig:
            out |= set(self.agent_map["categories"].get(d.category.value, []))
        return out

    @staticmethod
    def rule_coverage(report: DeltaReport) -> float:
        non_noise = [d for d in report.deltas if d.category != C.NOISE]
        return 1.0 if not non_noise else round(sum(1 for d in non_noise if d.classifier == "rule") / len(non_noise), 4)

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _d(cat, path, old, new, sig, why, rule, classifier="rule") -> Delta:
        return Delta(category=cat, field_path=path, old_value=old, new_value=new, significance=sig,
                     rationale=why, rule_id=rule, classifier=classifier)

    def _noise_delta(self, path, old, new, why, rule="R-NOISE") -> Delta:
        return self._d(C.NOISE, path, old, new, 0.0, why, rule)

    # ------------------------------------------------------------------ labs
    def _band(self, cfg: Optional[dict], prev: float) -> float:
        if not cfg:
            return self.rules["defaults"]["generic_noise_rel"] * abs(prev)
        n = cfg.get("noise", {})
        return max(n.get("abs", 0.0), n.get("rel", 0.0) * abs(prev))

    def _labs(self, pv: PatientView, cv: PatientView) -> List[Delta]:
        out: List[Delta] = []
        for err in cv.unit_errors:
            out.append(self._d(C.LAB_TREND_SHIFT, "latest_labs.?", None, None, self.rules["defaults"]["unclassified_significance"],
                               f"Unconvertible unit ({err}); fail-safe re-plan", "R-UNIT-UNKNOWN", "default"))
        for a in sorted(set(pv.labs) | set(cv.labs)):
            p, c = pv.labs.get(a), cv.labs.get(a)
            path = f"latest_labs.{a}"
            if c is None:
                continue  # lab no longer reported: data-quality issue, handled by PatientDataAgent
            cfg = self.rules["labs"].get(a)
            if p is None:
                out.append(self._new_lab(a, c, cfg, path))
                continue
            if c == p:
                continue
            out.append(self._lab_change(a, p, c, cfg, path))
        return out

    def _new_lab(self, a, c, cfg, path) -> Delta:
        if cfg:
            if a == "egfr" and c < cfg["thresholds"][0]:
                return self._d(C.ORGAN_FUNCTION_CHANGE, path, None, c, cfg["threshold_rule"]["significance"], f"New eGFR {c} is below 60", "R-EGFR-NEW")
            rr = cfg.get("reference_range")
            if rr and (c < rr["low"] or c > rr["high"]):
                return self._d(C.LAB_THRESHOLD_CROSSED, path, None, c, cfg["range_rule"]["significance"], f"New {a} {c} outside {rr['low']}-{rr['high']}", cfg["range_rule"]["id"] + "-NEW")
            if "uln" in cfg and c >= cfg["uln"] * cfg["multiple_rule"]["multiple"]:
                return self._d(C.LAB_THRESHOLD_CROSSED, path, None, c, cfg["multiple_rule"]["significance"], f"New {a} {c} >= {cfg['multiple_rule']['multiple']}xULN", cfg["multiple_rule"]["id"] + "-NEW")
            return self._d(C.LAB_TREND_SHIFT, path, None, c, self.rules["defaults"]["small_change_significance"], f"New {a} measurement within limits", f"R-{a.upper()}-NEW-OK")
        return self._d(C.LAB_TREND_SHIFT, path, None, c, self.rules["defaults"]["unclassified_significance"], f"New unmodelled analyte {a}", "R-UNMODELLED", "default")

    def _lab_change(self, a, p, c, cfg, path) -> Delta:
        band = self._band(cfg, p)
        ch = abs(c - p)
        cands: List[tuple] = []  # (significance, category, rule, why)
        suppressed_hysteresis: Optional[str] = None
        if cfg:
            unsafe = cfg.get("unsafe_direction")
            for t in sorted(cfg.get("thresholds", []), reverse=(unsafe == "up")):  # most severe crossing first
                if (p >= t) != (c >= t):
                    down = c < t
                    worsening = (unsafe == "down") == down
                    tr = cfg["threshold_rule"]
                    if worsening or ch > band:
                        cands.append((tr["significance"], C[tr["category"]], f"{tr['id']}-{t}", f"{a} crossed {t} ({p} -> {c})" + (" in the unsafe direction" if worsening else "")))
                    else:
                        suppressed_hysteresis = f"{a} crossed {t} back to the safe side within noise band {band:.2f}; hysteresis"
            rr = cfg.get("reference_range")
            if rr:
                pout, cout = (p < rr["low"] or p > rr["high"]), (c < rr["low"] or c > rr["high"])
                r = cfg["range_rule"]
                if cout and not pout:
                    cands.append((r["significance"], C[r["category"]], r["id"], f"{a} moved outside {rr['low']}-{rr['high']} ({p} -> {c})"))
                elif cout and pout and ch > band and abs(c - (rr["low"] if c < rr["low"] else rr["high"])) > abs(p - (rr["low"] if p < rr["low"] else rr["high"])):
                    cands.append((r["significance"], C[r["category"]], r["id"] + "-WORSE", f"{a} further outside range ({p} -> {c})"))
                elif pout and not cout:
                    if ch > band:
                        cands.append((0.6, C[r["category"]], r["id"] + "-RESOLVED", f"{a} returned to range ({p} -> {c})"))
                    else:
                        suppressed_hysteresis = f"{a} returned to range within noise band; hysteresis"
            if "uln" in cfg:
                m = cfg["multiple_rule"]
                T = cfg["uln"] * m["multiple"]
                if c >= T and (p < T or (c > p and ch > band)):
                    cands.append((m["significance"], C[m["category"]], m["id"], f"{a} {c} >= {m['multiple']}xULN ({T})"))
                elif p >= T > c:
                    if ch > band:
                        cands.append((0.6, C[m["category"]], m["id"] + "-RESOLVED", f"{a} fell below {m['multiple']}xULN"))
                    else:
                        suppressed_hysteresis = f"{a} fell below {m['multiple']}xULN within noise band; hysteresis"
            for mr in cfg.get("magnitude_rules", []):
                if ch >= mr["min_abs_change"]:
                    cands.append((mr["significance"], C[mr["category"]], mr["id"], f"{a} changed by {ch:.2f} (>= {mr['min_abs_change']})"))
        if cands:
            s, cat, rid, why = max(cands, key=lambda x: x[0])
            return self._d(cat, path, p, c, s, why, rid)
        if suppressed_hysteresis:
            return self._noise_delta(path, p, c, suppressed_hysteresis, "R-HYSTERESIS")
        if ch <= band:
            return self._noise_delta(path, p, c, f"{a} change {ch:.2f} within noise band {band:.2f}")
        if cfg:
            return self._d(C.LAB_TREND_SHIFT, path, p, c, self.rules["defaults"]["small_change_significance"], f"{a} change {ch:.2f} beyond noise but below rule thresholds", f"R-{a.upper()}-SMALL")
        return self._d(C.LAB_TREND_SHIFT, path, p, c, self.rules["defaults"]["unclassified_significance"], f"Unmodelled analyte {a} changed {p} -> {c}", "R-UNMODELLED", "default")

    # ------------------------------------------------------------------ vitals
    def _vitals(self, pv: PatientView, cv: PatientView) -> List[Delta]:
        out = []
        for a, cfg in self.rules["vitals"].items():
            p, c = pv.vitals.get(a), cv.vitals.get(a)
            if p is None or c is None or p == c:
                continue
            path, ch, band = f"vitals.{a}", abs(c - p), cfg["noise"]["abs"]
            ins = cfg["instability"]
            was, now = (p >= ins["high"] or p <= ins["low"]), (c >= ins["high"] or c <= ins["low"])
            crossed_up = [t for t in cfg.get("thresholds", []) if p < t <= c]
            if now and not was:
                out.append(self._d(C.VITAL_INSTABILITY, path, p, c, ins["significance"], f"{a} {c} in unstable range", ins["id"]))
            elif crossed_up:  # upward crossing of a decision threshold is never suppressed as noise
                out.append(self._d(C.VITAL_INSTABILITY, path, p, c, cfg["threshold_rule"]["significance"], f"{a} crossed {crossed_up[0]} ({p} -> {c})", f"{cfg['threshold_rule']['id']}-{crossed_up[0]}"))
            elif "shift" in cfg and ch >= cfg["shift"]["min_abs_change"]:
                out.append(self._d(C.VITAL_INSTABILITY, path, p, c, cfg["shift"]["significance"], f"{a} shifted by {ch:.0f}", cfg["shift"]["id"]))
            elif ch <= band:
                out.append(self._noise_delta(path, p, c, f"{a} change {ch:.0f} within noise band {band}"))
            else:
                out.append(self._d(C.VITAL_INSTABILITY, path, p, c, self.rules["defaults"]["small_change_significance"], f"{a} moderate change", f"R-{a.upper()}-SMALL"))
        return out

    # ------------------------------------------------------------------ conditions
    def _conditions(self, pv: PatientView, cv: PatientView) -> List[Delta]:
        cfg = self.rules["conditions"]

        def active(view: PatientView) -> Dict[str, str]:
            return {(c.code.upper() or c.display_name.lower()): c.display_name for c in view.state.active_conditions
                    if c.clinical_status in ("active", "recurrence", "relapse")}
        a, b = active(pv), active(cv)
        out = []
        for code in sorted(set(b) - set(a)):
            name = b[code]
            hi = any(k in name.lower() for k in cfg["new_high_impact"]["keywords"])
            r = cfg["new_high_impact"] if hi else cfg["new"]
            out.append(self._d(C.NEW_DIAGNOSIS, f"active_conditions.{code}", None, name, r["significance"], f"New diagnosis: {name}", r["id"]))
        for code in sorted(set(a) - set(b)):
            r = cfg["resolved"]
            out.append(self._d(C.RESOLVED_DIAGNOSIS, f"active_conditions.{code}", a[code], None, r["significance"], f"Diagnosis resolved: {a[code]}", r["id"]))
        return out

    # ------------------------------------------------------------------ medications
    def _meds(self, prev: PatientState, curr: PatientState) -> List[Delta]:
        cfg = self.rules["medications"]

        def keyed(s: PatientState):
            return {self.dk.normalise(m.name): m for m in s.active_medications if m.is_active}
        a, b = keyed(prev), keyed(curr)
        out = []
        for g in sorted(set(b) - set(a)):
            sig = cfg["added_default"]["significance"]
            for cls in self.dk.classes_of(g):
                sig = max(sig, cfg["added_high_risk"].get(cls, 0))
            out.append(self._d(C.MEDICATION_ADDED, f"active_medications.{g}", None, b[g].name, sig, f"New medication {g}", cfg["added_default"]["id"]))
        for g in sorted(set(a) - set(b)):
            out.append(self._d(C.MEDICATION_STOPPED, f"active_medications.{g}", a[g].name, None, cfg["stopped"]["significance"], f"Medication stopped: {g}", cfg["stopped"]["id"]))
        for g in sorted(set(a) & set(b)):
            oa, ob = (_norm_dose(a[g].dosage), (a[g].frequency or "").lower()), (_norm_dose(b[g].dosage), (b[g].frequency or "").lower())
            if oa != ob:
                out.append(self._d(C.DOSE_CHANGED, f"active_medications.{g}", f"{a[g].dosage} {a[g].frequency}", f"{b[g].dosage} {b[g].frequency}",
                                   cfg["dose_changed"]["significance"], f"Dose changed for {g}", cfg["dose_changed"]["id"]))
        return out

    # ------------------------------------------------------------------ allergies
    def _allergies(self, prev: PatientState, curr: PatientState) -> List[Delta]:
        cfg = self.rules["allergies"]
        a = {_norm_allergy(x.substance) for x in prev.allergies}
        b = {_norm_allergy(x.substance) for x in curr.allergies}
        out = [self._d(C.NEW_ALLERGY, f"allergies.{s}", None, s, cfg["new"]["significance"], f"New allergy: {s}", cfg["new"]["id"]) for s in sorted(b - a)]
        out += [self._d(C.ALLERGY_REMOVED, f"allergies.{s}", s, None, cfg["removed"]["significance"], f"Allergy removed: {s}", cfg["removed"]["id"]) for s in sorted(a - b)]
        return out

    # ------------------------------------------------------------------ demographics
    def _demographics(self, pv: PatientView, cv: PatientView) -> List[Delta]:
        cfg = self.rules["demographics"]
        out = []
        ap, ac = pv.age(), cv.age()
        for t in cfg["age_crossings"]:
            if ap < t <= ac:
                out.append(self._d(C.DEMOGRAPHIC_CHANGE, "demographics.age", round(ap, 1), round(ac, 1), cfg["age_crossing"]["significance"], f"Age crossed {t}", cfg["age_crossing"]["id"]))
        dp, dc = pv.state.demographics, cv.state.demographics
        if dp.gender != dc.gender:
            out.append(self._d(C.DEMOGRAPHIC_CHANGE, "demographics.gender", dp.gender, dc.gender, cfg["gender_change"]["significance"], "Gender field changed", cfg["gender_change"]["id"]))
        if (dp.race, dp.ethnicity) != (dc.race, dc.ethnicity):
            out.append(self._noise_delta("demographics.race_ethnicity", f"{dp.race}/{dp.ethnicity}", f"{dc.race}/{dc.ethnicity}", "Race/ethnicity edit is not decision-relevant in the rule set"))
        return out

    def _other(self, prev: PatientState, curr: PatientState) -> List[Delta]:
        out = []
        if prev.clinical_notes != curr.clinical_notes:
            out.append(self._noise_delta("clinical_notes", "...", "...", "Free-text change only; structured fields unchanged (no NLP extraction enabled - see limitations)", "R-NOTES"))
        if prev.procedures != curr.procedures:
            out.append(self._noise_delta("procedures", len(prev.procedures), len(curr.procedures), "Procedure list change is not decision-relevant in the rule set", "R-PROC"))
        return out
