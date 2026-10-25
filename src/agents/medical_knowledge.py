"""MedicalKnowledgeAgent: retrieves evidence for the patient's problems; abstains when nothing is retrievable."""
from __future__ import annotations

from typing import Dict, List

from src.agents.base import ClinicalAgent
from src.agents.common import Facts, mk_claim
from src.contracts.agent_io import AgentInput, AgentOutput
from src.contracts.evidence import EvidenceRef


class MedicalKnowledgeAgent(ClinicalAgent):
    ROLE = "Retrieve guideline/label evidence relevant to the patient's problems"
    READS = ("conditions", "medications", "labs", "allergies")

    def __init__(self, top_k: int = 2, **kw):
        super().__init__("medical_knowledge", **kw)
        self.top_k = top_k

    def _queries(self, f: Facts) -> List[str]:
        q = [f"{m} kidney eGFR safety" if m == "metformin" else f"{m} interaction safety" for m in f.meds]
        if f.labs.get("egfr", 999) < 60:
            q.append("chronic kidney disease eGFR category staging")
        if f.labs.get("potassium", 0) > 5.2:
            q.append("hyperkalemia potassium RAS inhibitor")
        if f.labs.get("alt", 0) >= 120:
            q.append("ALT liver injury hepatotoxic")
        if f.has_condition("diabetes"):
            q.append("A1C glycemic goal diabetes treatment")
        if f.age is not None and f.age >= 65:
            q.append("Beers criteria older adults potentially inappropriate")
        if f.pregnant:
            q.append("pregnancy fetal toxicity teratogenic")
        q += [f"{a} allergy avoid cross-reactivity" for a in f.allergies]
        return q

    def _run(self, inp: AgentInput) -> AgentOutput:
        f = Facts.from_upstream(inp)
        pool: Dict[str, EvidenceRef] = {}
        queries = self._queries(f)
        if not queries:
            return AgentOutput(agent_name=self.name, confidence=0.9, narrative="No problems requiring evidence retrieval.")
        for q in queries:
            for r in self.retriever.retrieve(q, self.top_k):
                if r.id not in pool or r.retrieval_score > pool[r.id].retrieval_score:
                    pool[r.id] = r
        refs = sorted(pool.values(), key=lambda r: (-r.retrieval_score, r.id))[:14]
        if not refs:
            return AgentOutput(agent_name=self.name, abstained=True, confidence=0.0,
                               abstention_reason="No evidence retrieved for this patient's problems (refusing to answer from model memory)")
        claims = [mk_claim(self.name, r.snippet.rstrip("."), [r], round(0.6 + 0.4 * r.retrieval_score, 2), "guideline", {"chunk": r.id}) for r in refs]
        conf = round(sum(r.retrieval_score for r in refs) / len(refs) * 0.5 + 0.5, 3)
        return AgentOutput(agent_name=self.name, claims=claims, evidence_pool=refs, confidence=min(conf, 0.95),
                           narrative=f"Retrieved {len(refs)} evidence passages from {len({r.id.split('#')[0] for r in refs})} sources.")
