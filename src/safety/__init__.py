"""Safety package: abstention, red flags, PHI guard."""
from src.safety.abstention import AbstentionPolicy, aggregate_confidence, ConfidenceBreakdown
from src.safety.red_flags import detect_red_flags, EMERGENCY_TEXT
from src.safety.phi import find_phi

__all__ = ["AbstentionPolicy", "aggregate_confidence", "ConfidenceBreakdown", "detect_red_flags", "EMERGENCY_TEXT", "find_phi"]
