"""EHRAnalysisAgent: longitudinal trajectories from the versioned state history."""
from __future__ import annotations

from src.agents.base import ClinicalAgent
from src.agents.common import ehr_ref, mk_claim
from src.contracts.agent_io import AgentInput, AgentOutput
from src.knowledge.patient_view import PatientView
from src.units import canonical_unit

_MIN_CHANGE = {"egfr": 3.0, "hba1c": 0.3, "potassium": 0.2, "creatinine": 0.1, "alt": 5.0}


class EHRAnalysisAgent(ClinicalAgent):
    ROLE = "Summarise clinical timeline and lab trajectories across state versions"
    READS = ("history", "labs", "conditions", "medications")

    def __init__(self, **kw):
        super().__init__("ehr_analysis", **kw)

    def _run(self, inp: AgentInput) -> AgentOutput:
        st = inp.patient_state
        hist = list(inp.history)
        if not hist and inp.previous_state is not None:
            hist = [inp.previous_state]
        hist = [h for h in hist if h.version < st.version] + [st]
        views = [(h.version, PatientView(h)) for h in hist]
        A, claims, lines = self.name, [], []
        cur = views[-1][1]
        for a in sorted(cur.labs):
            series = [(v, pv.labs[a]) for v, pv in views if a in pv.labs]
            if len(series) < 2:
                continue
            first, last = series[-2][1], series[-1][1]
            thr = _MIN_CHANGE.get(a, 0.05 * abs(first))
            direction = "stable" if abs(last - first) <= thr else ("rising" if last > first else "falling")
            vals = [round(x, 2) for _, x in series]
            unit = canonical_unit(a)
            txt = f"{a} trajectory over versions {series[0][0]}-{series[-1][0]}: {vals} {unit} ({direction})"
            lines.append(txt)
            claims.append(mk_claim(A, txt, [ehr_ref(st.patient_id, st.version, f"trajectory.{a}", txt)], 0.9,
                                   "trajectory", {"analyte": a, "values": vals, "direction": direction, "current": round(last, 4)}))
        if not lines:
            txt = "No prior encounters available: trajectory analysis not possible (first recorded state)."
            claims.append(mk_claim(A, txt, [ehr_ref(st.patient_id, st.version, "trajectory", txt)], 0.9, "trajectory", {"analyte": None, "values": [], "direction": "none"}))
            lines.append(txt)
        return AgentOutput(agent_name=A, claims=claims, confidence=0.9 if len(views) > 1 else 0.7, narrative=" ".join(lines))
