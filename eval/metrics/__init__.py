"""Evaluation metrics (all computed from real pipeline runs)."""
from eval.metrics.rsi import compute_rsi, recommendation_signature
from eval.metrics.adaptation_recall import compute_adaptation_recall, check_expectations
from eval.metrics.propagation_containment import FaultOutcome, compute_pcr, compute_detection_rate, breakdown
from eval.metrics.unsupported_claim_rate import compute_ucr
from eval.metrics.efficiency import reduction, mean
from eval.metrics.risk_coverage import aurc, risk_coverage_curve

__all__ = ["compute_rsi", "recommendation_signature", "compute_adaptation_recall", "check_expectations", "FaultOutcome", "compute_pcr",
           "compute_detection_rate", "breakdown", "compute_ucr", "reduction", "mean", "aurc", "risk_coverage_curve"]
