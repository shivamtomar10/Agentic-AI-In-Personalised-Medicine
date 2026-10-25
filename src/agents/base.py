"""Abstract Agent contract + concrete ClinicalAgent helper. Implements Part B.2 & C.5.

Rule 5: every execution is timed and written to the audit trail when an AuditLogger is attached.
"""
from __future__ import annotations

import time
from abc import ABC, abstractmethod
from typing import Any, Dict, Optional

from src.contracts.agent_io import AgentInput, AgentOutput


class Agent(ABC):
    """Abstract base for all specialised agents (plan / execute / report_status contract)."""

    def __init__(self, name: str, config: Dict[str, Any] | None = None):
        self.name = name
        self.config = config or {}

    @abstractmethod
    def plan(self, input_data: AgentInput) -> Dict[str, Any]:
        """Declare what this agent will read/retrieve/compute."""

    @abstractmethod
    def execute(self, input_data: AgentInput) -> AgentOutput:
        """Run the agent and return a typed AgentOutput."""

    @abstractmethod
    def report_status(self) -> Dict[str, Any]:
        """Operational status / configuration."""


class ClinicalAgent(Agent):
    """Template: subclasses implement ``_run``; timing, metadata and auditing are handled here."""

    READS: tuple = ()
    ROLE: str = ""

    def __init__(self, name: str, retriever=None, audit=None, config: Dict[str, Any] | None = None):
        super().__init__(name, config)
        self._retriever = retriever
        self.audit = audit
        self.runs = 0

    @property
    def retriever(self):
        if self._retriever is None:
            from src.retrieval.local import LocalKnowledgeRetriever
            self._retriever = LocalKnowledgeRetriever()
        return self._retriever

    def plan(self, input_data: AgentInput) -> Dict[str, Any]:
        return {"agent": self.name, "role": self.ROLE, "reads": list(self.READS), "mode": "deterministic_rules+retrieval"}

    def _run(self, inp: AgentInput) -> AgentOutput:  # pragma: no cover - abstract by convention
        raise NotImplementedError

    def execute(self, input_data: AgentInput) -> AgentOutput:
        t0 = time.perf_counter()
        out = self._run(input_data)
        ms = round((time.perf_counter() - t0) * 1000, 3)
        out.execution_metadata.update({"latency_ms": ms, "agent": self.name, "state_version": input_data.patient_state.version, "llm_calls": 0})
        self.runs += 1
        if self.audit is not None:
            self.audit.log_event("agent_executed", {
                "agent": self.name, "patient_id": input_data.patient_state.patient_id,
                "state_version": input_data.patient_state.version, "n_claims": len(out.claims),
                "confidence": out.confidence, "abstained": out.abstained, "latency_ms": ms,
                "data_sources": sorted({e.id.split("#")[0] for c in out.claims for e in c.evidence}),
            })
        return out

    def report_status(self) -> Dict[str, Any]:
        return {"agent": self.name, "role": self.ROLE, "healthy": True, "runs": self.runs, "llm": False}
