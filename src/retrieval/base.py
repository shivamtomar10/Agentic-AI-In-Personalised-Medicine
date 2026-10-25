"""Biomedical knowledge retriever interface.
Implements Evidence Retrieval with Claim-Level Provenance, Part B.1 & C.4.
"""

from abc import ABC, abstractmethod
from typing import List
from src.contracts.evidence import EvidenceRef


class BaseRetriever(ABC):
    """Abstract interface for all biomedical evidence retrieval connectors."""

    @abstractmethod
    def retrieve(self, query: str, top_k: int = 5) -> List[EvidenceRef]:
        """Execute hybrid search and return grounded EvidenceRef spans."""
        raise NotImplementedError
