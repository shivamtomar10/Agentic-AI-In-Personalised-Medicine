"""LangGraph wiring (Part C.7). Nodes execute REAL agents via PipelineRunner.

evaluate_delta -> {emergency | carry_forward | patient_data}
patient_data -> ehr_analysis -> (diagnostic || medical_knowledge || treatment) -> join -> risk_assessment
  -> validate -> {force_replan (cycle-guarded) | assemble} -> explain -> END
Validation and explanation are never skipped (AGENTS.md safety nodes).
"""
from __future__ import annotations

from typing import Optional

from langgraph.graph import END, START, StateGraph

from src.orchestrator.state import OrchestratorState

__all__ = ["build_orchestrator_graph"]


def build_orchestrator_graph(runner: Optional["PipelineRunner"] = None):  # noqa: F821
    if runner is None:
        from src.orchestrator.runner import PipelineRunner
        return PipelineRunner().graph
    g = StateGraph(OrchestratorState)
    g.add_node("evaluate_delta", runner.n_evaluate_delta)
    g.add_node("emergency", runner.n_emergency)
    g.add_node("carry_forward", runner.n_carry_forward)
    for a in ("patient_data", "ehr_analysis", "diagnostic", "medical_knowledge", "treatment", "risk_assessment"):
        g.add_node(a, runner.agent_node(a))
    g.add_node("join", runner.n_join)
    g.add_node("validate", runner.n_validate)
    g.add_node("force_replan", runner.n_force_replan)
    g.add_node("assemble", runner.n_assemble)
    g.add_node("explain", runner.n_explain)
    g.add_edge(START, "evaluate_delta")
    g.add_conditional_edges("evaluate_delta", runner.route_after_delta, {"emergency": "emergency", "carry_forward": "carry_forward", "patient_data": "patient_data"})
    g.add_edge("emergency", END)
    g.add_edge("carry_forward", "patient_data")
    g.add_edge("patient_data", "ehr_analysis")
    for a in ("diagnostic", "medical_knowledge", "treatment"):
        g.add_edge("ehr_analysis", a)
    g.add_edge(["diagnostic", "medical_knowledge", "treatment"], "join")
    g.add_edge("join", "risk_assessment")
    g.add_edge("risk_assessment", "validate")
    g.add_conditional_edges("validate", runner.route_after_validate, {"force_replan": "force_replan", "assemble": "assemble"})
    g.add_edge("force_replan", "patient_data")
    g.add_edge("assemble", "explain")
    g.add_edge("explain", END)
    return g.compile()
