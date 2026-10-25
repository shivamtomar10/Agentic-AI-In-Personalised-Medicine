"""Public, read-only library: search the curated medicines table and evidence corpus.

This is the site's dictionary-style feature. It only ever returns what is in data/knowledge (author-written
paraphrases of guidance, flagged as such) and never generates text. It gives no patient-specific advice.
"""
from __future__ import annotations

import re
from functools import lru_cache
from typing import Any, Dict, List

from fastapi import APIRouter, Query, Request

from src.api.deps import enforce_rate
from src.knowledge import get_drug_knowledge, get_evidence_store
from src.retrieval.local import LocalKnowledgeRetriever

router = APIRouter(prefix="/api/v1/knowledge")

NOTICE = ("General information from a small, curated library of guidance excerpts (paraphrased, not verbatim). "
          "It is not advice for any individual. Check the original source and ask a clinician.")


@lru_cache(maxsize=1)
def _retriever() -> LocalKnowledgeRetriever:
    return LocalKnowledgeRetriever()


def _tokens(q: str) -> List[str]:
    return re.findall(r"[a-z0-9][a-z0-9\-]*", q.lower())


def _medicine(name: str) -> Dict[str, Any]:
    dk = get_drug_knowledge()
    classes = sorted(dk.drugs.get(name, set()))
    interactions = []
    for rule in dk.rules:
        for mine, other in ((rule["a"], rule["b"]), (rule["b"], rule["a"])):
            if mine == name or mine in classes:
                interactions.append({"with": other.replace("_", " "), "severity": rule["severity"], "effect": rule["effect"]})
    seen, uniq = set(), []
    for i in interactions:
        k = (i["with"], i["effect"])
        if k not in seen:
            seen.add(k)
            uniq.append(i)
    allergy = dk.allergy_map.get(name)
    return {"name": name,
            "brand_names": sorted(a for a, g in dk.aliases.items() if g == name and a != name),
            "classes": [c.replace("_", " ") for c in classes],
            "interactions": uniq,
            "allergy_note": ({"same_class": [x.replace("_", " ") for x in allergy["definite"]],
                              "possible_cross_reactivity": [x.replace("_", " ") for x in allergy["possible"]]} if allergy else None)}


def _match_medicines(q: str, limit: int = 6) -> List[Dict[str, Any]]:
    dk = get_drug_knowledge()
    generics = set(dk.drugs) | set(dk.allergy_map)
    names = {n: dk.aliases.get(n, n) for n in list(generics) + list(dk.aliases)}
    scored: Dict[str, int] = {}
    for t in _tokens(q):
        if len(t) < 3:
            continue
        for n, g in names.items():
            score = 3 if n == t else 2 if n.startswith(t) else 0
            if score and scored.get(g, 0) < score:
                scored[g] = score
    ranked = sorted(scored, key=lambda g: (-scored[g], g))[:limit]
    return [_medicine(g) for g in ranked]


def _match_evidence(q: str, limit: int = 6) -> List[Dict[str, Any]]:
    store = get_evidence_store()
    refs = _retriever().retrieve(q, top_k=14)
    docs: Dict[str, Dict[str, Any]] = {}
    for r in refs:
        ch = store.chunks[r.id]
        d = docs.setdefault(ch.doc_id, {"id": ch.doc_id, "title": ch.title, "source": r.source.value, "url": r.url, "publication_date": r.publication_date,
                                        "verbatim": r.verbatim, "score": r.retrieval_score, "snippets": []})
        if len(d["snippets"]) < 2:
            d["snippets"].append(r.snippet)
    return sorted(docs.values(), key=lambda d: -d["score"])[:limit]


@router.get("/search")
def search(request: Request, q: str = Query(..., min_length=2, max_length=100)) -> Dict[str, Any]:
    enforce_rate(request, "search", 90, 60)
    q = q.strip()
    return {"query": q, "medicines": _match_medicines(q), "evidence": _match_evidence(q), "notice": NOTICE}


@router.get("/suggest")
def suggest(request: Request, q: str = Query(..., min_length=2, max_length=40)) -> Dict[str, Any]:
    enforce_rate(request, "suggest", 240, 60)
    dk = get_drug_knowledge()
    p = q.strip().lower()
    generics = set(dk.drugs) | set(dk.allergy_map)
    out = [{"term": n, "kind": "medicine" if n in generics else "brand name"} for n in sorted(generics | set(dk.aliases)) if n.startswith(p)]
    return {"suggestions": out[:8]}
