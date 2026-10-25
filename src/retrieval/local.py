"""Offline BM25 retriever over the curated evidence corpus (sparse only; dense/pgvector = future work)."""
from __future__ import annotations

import math
import re
from collections import Counter
from typing import Dict, List, Optional

from src.contracts.evidence import EvidenceRef
from src.knowledge.evidence_store import EvidenceStore, get_evidence_store
from src.retrieval.base import BaseRetriever

_STOP = {"the", "a", "an", "of", "and", "or", "in", "to", "is", "are", "for", "with", "on", "by", "be", "as", "at", "that", "it", "should", "when", "than", "may", "can"}

MEDICAL_EXPANSIONS: Dict[str, List[str]] = {
    "egfr": ["egfr", "gfr", "kidney", "renal", "glomerular", "filtration"],
    "hba1c": ["hba1c", "a1c", "glycemic", "glucose", "diabetes"],
    "alt": ["alt", "liver", "hepatic", "hepatotoxicity", "aminotransferase"],
    "rasi": ["rasi", "ace", "arb", "renin", "angiotensin", "potassium", "hyperkalemia"],
    "beers": ["beers", "inappropriate", "geriatric", "older"],
    "qt": ["qt", "torsades", "prolongation", "ecg"],
    "nsaid": ["nsaid", "ibuprofen", "naproxen", "bleeding", "anticoagulant"],
}


def tokenize(text: str) -> List[str]:
    toks = re.findall(r"[a-z0-9]+(?:\.[0-9]+)?", text.lower())
    return [t[:-1] if len(t) > 3 and t.endswith("s") else t for t in toks if t not in _STOP]


def expand_query(query: str) -> str:
    """Expands clinical abbreviations into domain synonyms to improve hybrid recall."""
    low = query.lower()
    extra = []
    for abbr, terms in MEDICAL_EXPANSIONS.items():
        if re.search(r"\b" + re.escape(abbr) + r"\b", low):
            extra.extend(terms)
    if extra:
        return query + " " + " ".join(dict.fromkeys(extra))
    return query


class RetrievalUnavailableError(RuntimeError):
    """Raised instead of silently substituting model memory (AGENTS.md Rule 6)."""


class LocalKnowledgeRetriever(BaseRetriever):
    def __init__(self, store: Optional[EvidenceStore] = None, k1: float = 1.5, b: float = 0.75):
        self.store = store or get_evidence_store()
        self.k1, self.b = k1, b
        self.docs: Dict[str, Counter] = {}
        for cid in self.store.order:
            ch = self.store.chunks[cid]
            self.docs[cid] = Counter(tokenize(ch.ref.snippet + " " + ch.title))
        self.avgdl = sum(sum(c.values()) for c in self.docs.values()) / max(1, len(self.docs))
        df: Counter = Counter()
        for c in self.docs.values():
            df.update(c.keys())
        n = len(self.docs)
        self.idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}
        
        # Precompute TF-IDF document vectors for dense cosine similarity calculation
        self.doc_vectors: Dict[str, Dict[str, float]] = {}
        self.doc_norms: Dict[str, float] = {}
        for cid, tf in self.docs.items():
            vec = {t: freq * self.idf.get(t, 0.0) for t, freq in tf.items()}
            norm = math.sqrt(sum(v * v for v in vec.values()))
            self.doc_vectors[cid] = vec
            self.doc_norms[cid] = norm if norm > 0 else 1.0

    def _cosine_similarity(self, q_tokens: List[str], cid: str) -> float:
        q_tf = Counter(q_tokens)
        q_vec = {t: freq * self.idf.get(t, 0.0) for t, freq in q_tf.items()}
        q_norm = math.sqrt(sum(v * v for v in q_vec.values()))
        if q_norm == 0 or cid not in self.doc_vectors:
            return 0.0
        doc_vec = self.doc_vectors[cid]
        dot = sum(q_vec[t] * doc_vec[t] for t in q_vec if t in doc_vec)
        return dot / (q_norm * self.doc_norms[cid])

    def retrieve(self, query: str, top_k: int = 5) -> List[EvidenceRef]:
        exp_q_str = expand_query(query)
        q_tokens = tokenize(exp_q_str)
        raw_q_tokens = tokenize(query)
        
        scores = []
        for cid, tf in self.docs.items():
            dl = sum(tf.values())
            s_bm25 = 0.0
            for t in q_tokens:
                if t in tf:
                    f = tf[t]
                    s_bm25 += self.idf.get(t, 0) * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * dl / self.avgdl))
            
            s_dense = self._cosine_similarity(raw_q_tokens, cid)
            # Hybrid combination: BM25 score + dense TF-IDF cosine similarity boost
            s_hybrid = 0.7 * s_bm25 + 0.3 * s_dense
            
            if s_hybrid > 0:
                scores.append((s_hybrid, cid))
                
        scores.sort(key=lambda x: (-x[0], x[1]))
        if not scores:
            return []
        top = scores[0][0]
        out = []
        for s, cid in scores[:top_k]:
            out.append(self.store.chunks[cid].ref.model_copy(update={"retrieval_score": round(min(1.0, s / top), 4)}))
        return out

    def by_topic(self, topic: str) -> List[EvidenceRef]:
        refs = self.store.by_topic(topic)
        if not refs:
            raise RetrievalUnavailableError(f"No evidence indexed for topic '{topic}'")
        return refs
