"""Memory layer contracts and stubs.
Part B.2 — Episodic encounter history and Semantic vector memory.
"""

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional


class EpisodicStore(ABC):
    """Encounter log storing chronological state interactions per patient."""

    @abstractmethod
    def log_encounter(self, patient_id: str, encounter_data: Dict[str, Any]) -> str:
        raise NotImplementedError

    @abstractmethod
    def get_encounters(self, patient_id: str) -> List[Dict[str, Any]]:
        raise NotImplementedError


class SemanticStore(ABC):
    """pgvector semantic memory for guideline chunks and literature retrieval."""

    @abstractmethod
    def search(self, query: str, top_k: int = 5) -> List[Dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    def index(self, documents: List[Dict[str, Any]]) -> int:
        raise NotImplementedError
