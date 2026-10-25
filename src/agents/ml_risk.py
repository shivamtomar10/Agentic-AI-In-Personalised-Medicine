"""MLRiskAgent: Uses trained ML models to generate probabilistic risk scores.

This agent operates ALONGSIDE the rule-based agents. It does NOT replace them.
It provides a supplementary ML-based risk score for CKD and Diabetes complications.
"""
from __future__ import annotations

from typing import List

from src.agents.base import ClinicalAgent
from src.contracts.agent_io import AgentInput, AgentOutput
from src.contracts.recommendation import RiskFlag, RiskSeverity
from src.ml.predictor import predictor


class MLRiskAgent(ClinicalAgent):
    ROLE = "Generate probabilistic risk scores using trained ML models on patient data"
    READS = ("demographics", "labs", "vitals", "conditions")

    def __init__(self, **kw):
        super().__init__("ml_risk", **kw)

    def _run(self, inp: AgentInput) -> AgentOutput:
        A = self.name
        st = inp.patient_state
        flags: List[RiskFlag] = []
        narratives = []
        
        # 1. CKD Risk Prediction
        ckd_result = predictor.predict_ckd_risk(st)
        if ckd_result:
            score = ckd_result["risk_score"]
            if score >= 0.75:
                flags.append(RiskFlag(
                    flag_id="ML-CKD-HIGH",
                    severity=RiskSeverity.HIGH,
                    category="organ_adjustment",
                    description=f"ML Model: High risk of CKD (Prob: {score:.2f})",
                    evidence=[],
                    mitigation_advice="Review renal function closely. Note: This is an ML-based alert, supplementary to clinical guidelines.",
                    source_agent=A,
                    involved=["serum_creatinine", "blood_urea"] if "serum_creatinine" in ckd_result["top_features"] else ckd_result["top_features"]
                ))
            elif score >= 0.4:
                flags.append(RiskFlag(
                    flag_id="ML-CKD-MOD",
                    severity=RiskSeverity.MODERATE,
                    category="organ_adjustment",
                    description=f"ML Model: Moderate risk of CKD (Prob: {score:.2f})",
                    evidence=[],
                    mitigation_advice="Monitor renal function. Note: This is an ML-based alert.",
                    source_agent=A,
                    involved=ckd_result["top_features"]
                ))
            narratives.append(f"CKD ML Model (RF) Score: {score:.2f}")

        # 2. Diabetes Risk Prediction
        diab_result = predictor.predict_diabetes_risk(st)
        if diab_result:
            score = diab_result["risk_score"]
            if score >= 0.7:
                flags.append(RiskFlag(
                    flag_id="ML-DIAB-HIGH",
                    severity=RiskSeverity.HIGH,
                    category="data_quality", # Reusing category, might need a generic one
                    description=f"ML Model: High risk of diabetes complication / poor control (Prob: {score:.2f})",
                    evidence=[],
                    mitigation_advice="Review glycaemic control. Note: This is an ML-based alert, supplementary to clinical guidelines.",
                    source_agent=A,
                    involved=["glucose"] if "Glucose" in diab_result["top_features"] else diab_result["top_features"]
                ))
            narratives.append(f"Diabetes ML Model (GB) Score: {score:.2f}")

        if not ckd_result and not diab_result:
            return AgentOutput(
                agent_name=A,
                abstained=True,
                abstention_reason="Insufficient numeric data to run ML models.",
                confidence=1.0
            )

        return AgentOutput(
            agent_name=A,
            risk_flags=flags,
            confidence=0.8,
            narrative="; ".join(narratives) + ". ML scores are supplementary to rule-based guidelines."
        )
