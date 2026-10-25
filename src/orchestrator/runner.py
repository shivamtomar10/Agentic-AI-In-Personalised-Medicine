"""PipelineRunner: real agent execution behind the LangGraph graph (replaces the scaffold's no-op nodes).

Implements selective re-planning (Temporal Personalization), safety nodes that are never skipped,
cycle/budget/timeout guards, provenance gating, conflict surfacing and calibrated abstention.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

from src.agents import (DiagnosticAgent, EHRAnalysisAgent, ExplanationAgent, MedicalKnowledgeAgent, PatientDataAgent,
                        RiskAssessmentAgent, TreatmentAgent, ValidationAgent, MLRiskAgent)
from src.agents.common import Facts, MissingUpstreamError
from src.audit.logger import AuditLogger, NullAudit
from src.config import get_settings
from src.contracts.agent_io import AgentInput, AgentOutput, ExecutionMode
from src.contracts.delta import DeltaReport
from src.contracts.evidence import ClaimStatus, ClinicalClaim
from src.contracts.patient import PatientState
from src.contracts.recommendation import (AgentTraceEntry, ClinicalAction, Recommendation, RecommendationStatus, RunMetrics, RiskFlag)
from src.knowledge.evidence_store import get_evidence_store
from src.llm.client import LLMClient
from src.orchestrator.escalation import check_trigger as esc_check_trigger, escalate as esc_run
from src.orchestrator.policy import (CONTEXT_AGENTS, CRITICAL_FOR_ABSTENTION, InvocationBudgetExceeded, PipelineTimeout,
                                     REASONING_AGENTS, resolve_conflicts)
from src.provenance.gate import ProvenanceGate
from src.retrieval.local import LocalKnowledgeRetriever
from src.safety.abstention import AbstentionPolicy, aggregate_confidence
from src.safety.calibration import load_calibrator
from src.safety.red_flags import detect_red_flags
from src.state.delta_engine import DeltaEngine

FaultHook = Callable[[str, AgentOutput, AgentInput], AgentOutput]


@dataclass
class PipelineConfig:
    enable_validation: bool = True
    enable_provenance_gate: bool = True
    enable_abstention: bool = True
    enable_safety_replan: bool = True
    replan_threshold: Optional[float] = None
    abstention_threshold: Optional[float] = None
    max_invocations: Optional[int] = None
    timeout_s: Optional[float] = None
    fault_hook: Optional[FaultHook] = None
    llm: Optional[LLMClient] = None
    llm_mode: Optional[str] = None
    enable_llm_escalation: Optional[bool] = None
    llm_escalation_margin: Optional[float] = None


@dataclass
class RunResult:
    recommendation: Recommendation
    outputs: Dict[str, AgentOutput]
    delta_report: Optional[DeltaReport]
    state: PatientState
    anchor: Optional[PatientState] = None   # state on which the cached reasoning was last actually (re)computed


class PipelineRunner:
    def __init__(self, config: Optional[PipelineConfig] = None, audit=None, retriever=None):
        s = get_settings()
        self.cfg = config or PipelineConfig()
        self.audit = audit if audit is not None else NullAudit()
        self.retriever = retriever or LocalKnowledgeRetriever()
        self.threshold = self.cfg.replan_threshold if self.cfg.replan_threshold is not None else s.replan_threshold
        self.max_inv = self.cfg.max_invocations or s.max_agent_invocations
        self.timeout = self.cfg.timeout_s or s.run_timeout_s
        self.delta_engine = DeltaEngine(threshold=self.threshold)
        self.gate = ProvenanceGate()
        self.calibrator = load_calibrator()
        self.abstention = AbstentionPolicy(self.cfg.abstention_threshold if self.cfg.abstention_threshold is not None else s.abstention_threshold, self.cfg.enable_abstention)
        self.llm_mode = self.cfg.llm_mode if self.cfg.llm_mode is not None else s.llm_mode
        if self.cfg.llm is None and self.llm_mode == "hybrid":
            from src.llm import get_llm_client
            self.cfg.llm = get_llm_client()
        self.enable_llm_escalation = self.cfg.enable_llm_escalation if self.cfg.enable_llm_escalation is not None else s.enable_llm_escalation
        self.llm_escalation_margin = self.cfg.llm_escalation_margin if self.cfg.llm_escalation_margin is not None else s.llm_escalation_margin
        kw = dict(retriever=self.retriever, audit=self.audit)
        self.agents = {
            "patient_data": PatientDataAgent(audit=self.audit), "ehr_analysis": EHRAnalysisAgent(audit=self.audit),
            "diagnostic": DiagnosticAgent(**kw), "medical_knowledge": MedicalKnowledgeAgent(**kw),
            "treatment": TreatmentAgent(**kw), "risk_assessment": RiskAssessmentAgent(**kw),
            "ml_risk": MLRiskAgent(**kw),
            "validation": ValidationAgent(audit=self.audit), "explanation": ExplanationAgent(llm=self.cfg.llm, audit=self.audit),
        }
        from src.orchestrator.graph import build_orchestrator_graph
        self.graph = build_orchestrator_graph(self)

    # ------------------------------------------------------------------ public API
    def run(self, curr: PatientState, prev: Optional[PatientState] = None, history: Optional[List[PatientState]] = None,
            prev_run: Optional[RunResult] = None, mode: ExecutionMode = ExecutionMode.SELECTIVE_REPLAN,
            texts: Optional[List[str]] = None) -> RunResult:
        init = {
            "patient_state": curr, "previous_state": prev, "history": history or ([prev] if prev else []),
            "texts": list(texts or []) + list(curr.clinical_notes), "mode": mode,
            "previous_outputs": prev_run.outputs if prev_run else {}, "previous_recommendation": prev_run.recommendation if prev_run else None,
            "agent_outputs": {}, "trace": [], "reasoning_invocations": 0, "context_invocations": 0, "safety_invocations": 0,
            "cycles": 0, "safety_replans": 0, "emergency": [], "affected": [], "started": time.perf_counter(),
            "anchor_state": (prev_run.anchor or prev_run.state) if prev_run else None,
        }
        final = self.graph.invoke(init)
        rec: Recommendation = final["recommendation"]
        anchor = init["anchor_state"] if final.get("route") == "carry" and init["anchor_state"] is not None else curr
        return RunResult(recommendation=rec, outputs=final.get("agent_outputs", {}), delta_report=final.get("delta_report"), state=curr, anchor=anchor)

    # ------------------------------------------------------------------ guards
    def _guard(self, state) -> None:
        if time.perf_counter() - state["started"] > self.timeout:
            raise PipelineTimeout(f"Run exceeded {self.timeout}s")
        total = state.get("reasoning_invocations", 0) + state.get("context_invocations", 0) + state.get("safety_invocations", 0)
        if total >= self.max_inv:
            raise InvocationBudgetExceeded(f"Agent invocation budget {self.max_inv} reached")

    # ------------------------------------------------------------------ nodes
    def n_evaluate_delta(self, state):
        curr, prev, mode = state["patient_state"], state.get("previous_state"), state["mode"]
        red = detect_red_flags(state.get("texts", []), curr)
        if red:
            return {"emergency": red, "route": "emergency", "route_reason": "red-flag short-circuit"}
        # Selective mode compares against the ANCHOR (state the cached reasoning was computed on), not just the previous
        # encounter: otherwise slow drift made of individually sub-noise steps would never trigger a re-plan.
        ref = state.get("anchor_state") if (mode == ExecutionMode.SELECTIVE_REPLAN and state.get("previous_outputs") and state.get("anchor_state") is not None) else prev
        report = self.delta_engine.compute(ref, curr) if ref is not None else None
        if mode == ExecutionMode.FULL_RERUN:
            return {"delta_report": report, "route": "full", "route_reason": "full re-run baseline (no memory of prior reasoning)"}
        if prev is None or not state.get("previous_outputs"):
            return {"delta_report": report, "route": "full", "route_reason": "first encounter / no cached reasoning"}
        affected = sorted(self.delta_engine.affected_agents(report))
        if not report.is_clinically_significant:
            return {"delta_report": report, "route": "carry", "affected": [], "route_reason": f"max significance {report.max_significance:.2f} < threshold {self.threshold:.2f}"}
        return {"delta_report": report, "route": "selective", "affected": affected, "route_reason": f"significant change; re-plan {affected}"}

    def route_after_delta(self, state) -> str:
        return "emergency" if state["route"] == "emergency" else ("carry_forward" if state["route"] == "carry" else "patient_data")

    def n_carry_forward(self, state):
        return {}  # decision recorded in route/route_reason; reasoning nodes below reuse cached outputs

    def agent_node(self, name: str):
        group = "context_invocations" if name in CONTEXT_AGENTS else "reasoning_invocations"

        def node(state):
            self._guard(state)
            route, prev_outs = state["route"], state.get("previous_outputs", {})
            run = name in CONTEXT_AGENTS or route == "full" or (route == "selective" and name in state.get("affected", [])) or name not in prev_outs
            if not run:
                status = "skipped_carry_forward" if route == "carry" else "reused_cached"
                out = prev_outs[name]
                why = state["route_reason"] if route == "carry" else "no relevant delta for this agent"
                return {"agent_outputs": {name: out}, "trace": [AgentTraceEntry(agent=name, status=status, reason=why, confidence=out.confidence, n_claims=len(out.claims))]}
            inp = AgentInput(agent_name=name, patient_state=state["patient_state"], previous_state=state.get("previous_state") if state["mode"] != ExecutionMode.FULL_RERUN else None,
                             history=state.get("history", []), delta_report=state.get("delta_report") if state["mode"] != ExecutionMode.FULL_RERUN else None,
                             upstream_outputs=dict(state["agent_outputs"]), execution_mode=state["mode"])
            out = self.agents[name].execute(inp)
            if self.cfg.fault_hook is not None:
                out = self.cfg.fault_hook(name, out, inp)
            return {"agent_outputs": {name: out}, group: 1,
                    "trace": [AgentTraceEntry(agent=name, status="abstained" if out.abstained else "ran", reason=out.abstention_reason or state["route_reason"],
                                              confidence=out.confidence, latency_ms=out.execution_metadata.get("latency_ms", 0.0), n_claims=len(out.claims))]}
        return node

    def n_join(self, state):
        return {}

    def n_validate(self, state):
        self._guard(state)
        if not self.cfg.enable_validation:
            return {"validation": None}
        inp = AgentInput(agent_name="validation", patient_state=state["patient_state"], upstream_outputs=dict(state["agent_outputs"]))
        out = self.agents["validation"].execute(inp)
        if self.cfg.fault_hook is not None:
            out = self.cfg.fault_hook("validation", out, inp)
        rep = out.validation_report
        return {"validation": rep, "safety_invocations": 1, "agent_outputs": {"validation": out},
                "trace": [AgentTraceEntry(agent="validation", status="ran", reason=out.narrative or "", confidence=out.confidence, latency_ms=out.execution_metadata.get("latency_ms", 0.0))]}

    def route_after_validate(self, state) -> str:
        rep = state.get("validation")
        if (self.cfg.enable_safety_replan and rep is not None and rep.unsafe_upstream and state["route"] in ("carry", "selective") and state.get("cycles", 0) < 1):
            return "force_replan"
        return "assemble"

    def n_force_replan(self, state):
        return {"route": "full", "route_reason": "safety re-plan: validation rejected reused reasoning against the current record",
                "cycles": state.get("cycles", 0) + 1, "safety_replans": state.get("safety_replans", 0) + 1, "affected": []}

    # ------------------------------------------------------------------ assemble
    def n_assemble(self, state):
        outs: Dict[str, AgentOutput] = state["agent_outputs"]
        st: PatientState = state["patient_state"]
        rep = state.get("validation")
        claims: Dict[str, ClinicalClaim] = {}
        for n, o in outs.items():
            if n in ("validation", "explanation"):
                continue
            for c in o.claims:
                claims.setdefault(c.claim_id, c)
        blocked = set(rep.blocked_claim_ids) if rep else set()
        if self.cfg.enable_provenance_gate:
            supported, unsupported = self.gate.partition(list(claims.values()))
        else:
            supported, unsupported = list(claims.values()), []
        flagged: List[ClinicalClaim] = list(unsupported)
        keep: List[ClinicalClaim] = []
        for c in supported:
            (flagged if c.claim_id in blocked else keep).append(c.model_copy(update={"status": ClaimStatus.REFUTED}) if c.claim_id in blocked else c)
        ok_ids = {c.claim_id for c in keep}
        unsafe = bool(rep and rep.unsafe_upstream)
        actions = outs["treatment"].actions if "treatment" in outs else []
        flags = outs["risk_assessment"].risk_flags if "risk_assessment" in outs else []
        good_actions = [a for a in actions if a.claim_ids and all(i in ok_ids for i in a.claim_ids)] if self.cfg.enable_provenance_gate or blocked else list(actions)
        bad_actions = [a for a in actions if a not in good_actions]
        if self.cfg.enable_provenance_gate or blocked:
            good_flags = [f for f in flags if (f.claim_id in ok_ids) or (f.claim_id is None and f.evidence)]
        else:
            good_flags = list(flags)
        bad_flags = [f for f in flags if f not in good_flags]
        conflicts = resolve_conflicts(good_actions, good_flags)
        stale = self.gate.stale_evidence(keep)
        n_ev = len({e.id for c in keep for e in c.evidence})
        confs = [o.confidence for n, o in outs.items() if n in REASONING_AGENTS + CONTEXT_AGENTS and not o.abstained]
        pdo = outs.get("patient_data")
        n_missing = sum(1 for q in (pdo.data_quality_flags if pdo else []) if q.startswith(("Missing", "No renal")))
        unknown = (pdo.execution_metadata.get("unknown_meds", []) if pdo else [])
        n_meds = max(1, (pdo.execution_metadata.get("n_meds", 0) if pdo else 0))
        b = aggregate_confidence(confs, len(keep), len(claims), len(conflicts), len(rep.findings) if rep else 0, len(stale), n_ev,
                                 completeness=max(0.0, 1.0 - 0.34 * n_missing), knowledge=1.0 - len(unknown) / n_meds)
        if self.calibrator is not None:
            b.calibrated = self.calibrator.predict(b.features())
        crit = next((n for n in CRITICAL_FOR_ABSTENTION if n in outs and outs[n].abstained), None)
        reason = self.abstention.decide(b, crit)
        if unsafe:
            det = "; ".join(sorted({f.detail for f in rep.findings if f.severity == "high"})[:3])
            reason = f"Validation found a fact-level inconsistency or a missing safety flag ({det}); output withheld and escalated"
        deferred = reason is not None
        if deferred:
            bad_actions = list(actions) if unsafe else bad_actions + good_actions
            good_actions = []
            if unsafe:
                bad_flags, good_flags = list(flags), []
        llm_note = None
        if self.llm_mode == "hybrid" and not deferred and not unsafe and not state.get("emergency"):
            from src.llm.hybrid import run_hybrid_llm
            accepted_llm, withheld_llm, llm_note = run_hybrid_llm(
                self.cfg.llm, st, good_actions, good_flags, self.audit
            )
            good_actions.extend(accepted_llm)
            bad_actions.extend(withheld_llm)
        llm_escalation: Optional[Dict] = None
        if self.enable_llm_escalation and not deferred and not unsafe:
            trig = esc_check_trigger(b.aggregate, self.llm_escalation_margin, self.abstention.threshold, len(conflicts), len(unsupported))
            try:
                pdo_for_facts = outs.get("patient_data")
                facts = Facts.from_upstream(AgentInput(agent_name="escalation", patient_state=st, upstream_outputs={"patient_data": pdo_for_facts})) if pdo_for_facts else None
            except MissingUpstreamError:
                facts = None
            if facts is not None:
                mko = outs.get("medical_knowledge")
                pool = list(mko.evidence_pool) if mko else []
                llm_escalation = esc_run(self.cfg.llm, facts, [fl.flag_id for fl in good_flags], trig, pool)
                self.audit.log_event("llm_escalation", {"patient_id": st.patient_id, "triggered": llm_escalation.get("triggered"),
                                                        "status": llm_escalation.get("status"), "reason": llm_escalation.get("trigger_reason")})
        draft_rxs = outs["treatment"].draft_prescriptions if "treatment" in outs else []
        rec = Recommendation(
            recommendation_id=f"REC-{st.patient_id}-v{st.version}-{uuid.uuid4().hex[:6]}", patient_id=st.patient_id, encounter_id=st.encounter_id,
            state_version=st.version, execution_mode=state["mode"].value,
            status=RecommendationStatus.ESCALATED if deferred else RecommendationStatus.REQUIRES_CLINICIAN_REVIEW,
            clinical_actions=good_actions, draft_prescriptions=draft_rxs,
            supported_claims=keep, flagged_claims=flagged, flagged_actions=bad_actions,
            risk_flags=good_flags, flagged_risk_flags=bad_flags, validation_report=rep, conflicts=conflicts, stale_evidence_ids=stale,
            deferred=deferred, abstention_reason=reason, aggregate_confidence=b.aggregate, calibrated_confidence=b.calibrated, confidence_components=b.features(),
            llm_escalation=llm_escalation,
            what_changed=self._what_changed(state, good_actions), delta_report=state.get("delta_report"), trace=self._final_trace(state),
            metrics=RunMetrics(reasoning_invocations=state.get("reasoning_invocations", 0), context_invocations=state.get("context_invocations", 0),
                               safety_invocations=state.get("safety_invocations", 0) + 1,
                               agent_invocations=state.get("reasoning_invocations", 0) + state.get("context_invocations", 0) + state.get("safety_invocations", 0) + 1,
                               wall_clock_ms=round((time.perf_counter() - state["started"]) * 1000, 2), cycles=state.get("cycles", 0), safety_replans=state.get("safety_replans", 0)),
            explanation="(pending)")
        rec._llm_note = llm_note
        return {"recommendation": rec}

    def _final_trace(self, state) -> List[AgentTraceEntry]:
        latest: Dict[str, AgentTraceEntry] = {}
        for t in state.get("trace", []):
            latest[t.agent] = t
        order = list(CONTEXT_AGENTS) + list(REASONING_AGENTS) + ["validation"]
        return [latest[a] for a in order if a in latest]

    def _what_changed(self, state, actions: List[ClinicalAction]) -> List[str]:
        out: List[str] = []
        rep: Optional[DeltaReport] = state.get("delta_report")
        prev: Optional[PatientState] = state.get("previous_state")
        if rep is not None and prev is not None:
            sig = [d for d in rep.deltas if d.significance >= self.threshold]
            ref_v = rep.previous_version
            if sig:
                out.append(f"Since v{ref_v}: " + "; ".join(f"{d.rationale} [{d.rule_id}, significance {d.significance:.2f}]" for d in sig) + ".")
            else:
                out.append(f"No clinically significant change since v{ref_v} (max significance {rep.max_significance:.2f}); the earlier reasoning was reused after re-checking it against the current record.")
        elif prev is None:
            out.append("First recorded encounter for this patient: full analysis performed.")
        pr: Optional[Recommendation] = state.get("previous_recommendation")
        if pr is not None:
            a = {(x.action_type, x.target) for x in pr.clinical_actions}
            b = {(x.action_type, x.target) for x in actions}
            for t in sorted(b - a):
                out.append(f"New proposed action: {t[0]} {t[1]}.")
            for t in sorted(a - b):
                out.append(f"No longer proposed: {t[0]} {t[1]}.")
        if state.get("route") == "full" and state["mode"] == ExecutionMode.SELECTIVE_REPLAN and state.get("safety_replans"):
            out.append("A safety re-plan was triggered because validation rejected reused reasoning.")
        return out

    def n_explain(self, state):
        rec: Recommendation = state["recommendation"]
        inp = AgentInput(agent_name="explanation", patient_state=state["patient_state"], context={"draft": rec})
        out = self.agents["explanation"].execute(inp)
        rec.explanation = out.narrative or "No explanation generated."
        note = getattr(rec, "_llm_note", None)
        if note and note not in rec.explanation:
            rec.explanation += f"\n[{note}]"
        rec.trace = rec.trace + [AgentTraceEntry(agent="explanation", status="ran", reason="always runs (safety node)", confidence=out.confidence, latency_ms=out.execution_metadata.get("latency_ms", 0.0))]
        self.audit.log_event("recommendation_created", {"recommendation_id": rec.recommendation_id, "patient_id": rec.patient_id, "state_version": rec.state_version,
                                                        "mode": rec.execution_mode, "deferred": rec.deferred, "n_actions": len(rec.clinical_actions), "n_flagged_claims": len(rec.flagged_claims),
                                                        "aggregate_confidence": rec.aggregate_confidence, "metrics": rec.metrics.model_dump()})
        return {"recommendation": rec, "safety_invocations": 1}

    def n_emergency(self, state):
        st = state["patient_state"]
        rec = Recommendation(recommendation_id=f"REC-{st.patient_id}-v{st.version}-{uuid.uuid4().hex[:6]}", patient_id=st.patient_id, encounter_id=st.encounter_id,
                             state_version=st.version, execution_mode=state["mode"].value, status=RecommendationStatus.ESCALATED, deferred=True,
                             abstention_reason="Emergency red flags detected; automated analysis bypassed", emergency_flags=state["emergency"],
                             metrics=RunMetrics(), explanation="(pending)")
        rec.explanation = ExplanationAgent.render(rec)
        self.audit.log_event("emergency_escalation", {"patient_id": st.patient_id, "flags": state["emergency"]})
        return {"recommendation": rec}
