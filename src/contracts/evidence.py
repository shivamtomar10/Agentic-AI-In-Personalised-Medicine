"""Evidence and clinical claim contracts.
Implements Provenance Architecture, Part A.3 (G2) & AGENTS.md Constitution Rule 4.
"""

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field, model_validator


class SourceType(str, Enum):
    """Types of retrieved sources supported in evidence linking."""
    PUBMED = "pubmed"
    EUROPE_PMC = "europe_pmc"
    OPENFDA = "openfda"
    GUIDELINE = "guideline"
    RXNORM = "rxnorm"
    DDINTER = "ddinter"
    LOCAL_EHR = "local_ehr"


class EvidenceRef(BaseModel):
    """Resolvable reference to a retrieved medical source span with a stable ID.
    Implements mandatory provenance requirement (AGENTS.md Rule 4).
    """
    id: str = Field(..., description="Stable unique identifier for the evidence span")
    source: SourceType = Field(..., description="Origin source type of the evidence")
    title: Optional[str] = Field(None, description="Document title or label")
    url: Optional[str] = Field(None, description="Resolvable URL to the original source")
    chunk_id: Optional[str] = Field(None, description="Identifier of the vector or text chunk")
    char_span: Optional[tuple[int, int]] = Field(None, description="Start and end character indices within chunk")
    retrieval_score: float = Field(default=1.0, ge=0.0, le=1.0, description="Similarity or relevance score")
    publication_date: Optional[str] = Field(None, description="Date of publication (YYYY-MM-DD) for staleness checks")
    patient_population: Optional[str] = Field(None, description="Target patient demographic or population context")
    version: Optional[str] = Field(None, description="Guideline or source version identifier")
    retrieval_timestamp: Optional[str] = Field(None, description="ISO timestamp of when evidence was fetched")
    verification_status: str = Field(default="supported", description="Verification status: supported, conflicting, outdated, or unavailable")
    snippet: str = Field(..., min_length=1, description="Source excerpt. For the curated offline corpus this is a paraphrase (see verbatim)")
    verbatim: bool = Field(default=False, description="True only if snippet is an exact quotation of the source")



class ClaimStatus(str, Enum):
    """Validation and grounding status of an atomic clinical claim."""
    PENDING = "pending"
    SUPPORTED = "supported"
    UNSUPPORTED = "unsupported"
    REFUTED = "refuted"


class ClaimPayload(BaseModel):
    """Machine-checkable structure behind a claim so the ValidationAgent can verify it.

    kind: lab_value | drug_interaction | contraindication | allergy_conflict |
          guideline | differential | data_quality | trajectory | action_basis
    """
    kind: str
    data: Dict[str, Any] = Field(default_factory=dict)


class ClinicalClaim(BaseModel):
    """Atomic clinical assertion produced by an agent.
    Each claim must carry an evidence list. Claims with empty evidence
    are automatically flagged as 'unsupported' (AGENTS.md Rule 4).
    """
    claim_id: str = Field(..., description="Unique claim identifier")
    assertion: str = Field(..., min_length=3, description="Single atomic clinical assertion")
    confidence: float = Field(..., ge=0.0, le=1.0, description="Model or rule confidence in [0, 1]")
    evidence: List[EvidenceRef] = Field(default_factory=list, description="Resolvable source spans")
    status: ClaimStatus = Field(default=ClaimStatus.PENDING, description="Provenance and grounding status")
    agent_source: str = Field(..., description="Name of the agent generating this assertion")
    structured: Optional[ClaimPayload] = Field(None, description="Verifiable structure of the assertion")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), description="Timestamp of claim creation")

    @model_validator(mode="after")
    def enforce_provenance_rule(self) -> "ClinicalClaim":
        """Enforces Constitution Rule 4: Claims without evidence are flagged unsupported."""
        if not self.evidence and self.status == ClaimStatus.SUPPORTED:
            raise ValueError("A claim cannot have status 'supported' with empty evidence.")
        if not self.evidence and self.status == ClaimStatus.PENDING:
            self.status = ClaimStatus.UNSUPPORTED
        return self
