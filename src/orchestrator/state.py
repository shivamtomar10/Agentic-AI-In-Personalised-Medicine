"""LangGraph state schema with reducers so parallel branches merge safely."""
from __future__ import annotations

import operator
from typing import Annotated, Any, Dict, List, Optional, TypedDict

from src.contracts.agent_io import AgentOutput, ExecutionMode
from src.contracts.delta import DeltaReport
from src.contracts.patient import PatientState
from src.contracts.recommendation import AgentTraceEntry, Recommendation, ValidationReport


def merge_dicts(a: Optional[dict], b: Optional[dict]) -> dict:
    return {**(a or {}), **(b or {})}


class OrchestratorState(TypedDict, total=False):
    patient_state: PatientState
    previous_state: Optional[PatientState]
    anchor_state: Optional[PatientState]   # state the cached reasoning was last computed on (drift reference)
    history: List[PatientState]
    texts: List[str]
    mode: ExecutionMode
    previous_outputs: Dict[str, AgentOutput]
    previous_recommendation: Optional[Recommendation]
    delta_report: Optional[DeltaReport]
    affected: List[str]
    route: str                       # full | selective | carry | emergency
    route_reason: str
    agent_outputs: Annotated[Dict[str, AgentOutput], merge_dicts]
    trace: Annotated[List[AgentTraceEntry], operator.add]
    reasoning_invocations: Annotated[int, operator.add]
    context_invocations: Annotated[int, operator.add]
    safety_invocations: Annotated[int, operator.add]
    cycles: int
    safety_replans: int
    validation: Optional[ValidationReport]
    recommendation: Optional[Recommendation]
    emergency: List[str]
    started: float
