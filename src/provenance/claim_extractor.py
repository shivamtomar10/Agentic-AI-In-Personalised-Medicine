"""Claim decomposition and evidence linking. Implements Part A.3 G2 / C.4.

NOTE (honest limitation): entailment is approximated lexically (entity + number agreement + token
overlap), not by an NLI model. It is conservative: a claim binds only if every drug name and every
number in it is present in the evidence span. Swap in an NLI cross-encoder for production.
"""
from __future__ import annotations

import re
import uuid
from typing import List

from src.contracts.evidence import ClaimStatus, ClinicalClaim, EvidenceRef
from src.knowledge.drugs import get_drug_knowledge
from src.retrieval.local import tokenize

_SENT = re.compile(r"(?<=[a-z0-9\)])\.\s+(?=[A-Z])|;\s+")
_NUM = re.compile(r"\d+(?:\.\d+)?")
_CMP = re.compile(r"(below|under|less than|<|above|over|greater than|>|at least)\s*(\d+(?:\.\d+)?)")
_PRED = ("contraindicat", "lethal", "fatal", "cure", "discontinu", "stopped", "avoid", "safe", "first-line")


def _dir(w: str) -> str:
    return "lt" if w in ("below", "under", "less than", "<") else "gt"


class ClaimExtractor:
    """Decomposes narrative text into atomic ClinicalClaim units (one assertion each)."""

    def extract(self, text: str, agent_source: str) -> List[ClinicalClaim]:
        claims = []
        lines = [re.sub(r"^\s*(?:[-*\u2022]|\d+[.)])\s*", "", ln) for ln in text.strip().splitlines()]   # one assertion per line, bullets stripped
        for part in [p for ln in lines for p in _SENT.split(ln.strip())]:
            for sub in re.split(r",\s+and\s+(?=[A-Z][a-z]+ (?:is|are|should|can|may|must))", part):
                sub = sub.strip().rstrip(".")
                if len(sub) >= 3:
                    claims.append(ClinicalClaim(claim_id=f"CLM-{uuid.uuid4().hex[:8]}", assertion=sub, confidence=0.5, agent_source=agent_source))
        return claims


_NUMWORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "twenty": 20, "thirty": 30}
_SYN = [(r"\b(?:stopped|stop|stops|withdrawn|withdraw|ceased|discontinue)\b", "discontinued"), (r"\b(?:has to be|have to be|must be)\b", "should be"),
        (r"\b(?:liver|hepatic) toxicity\b", "hepatotoxicity"), (r"\ballergic\b", "allergy"), (r"\b(?:kidney|renal) (?:impairment|insufficiency)\b", "renal insufficiency"),
        (r"\bhigh\b", "elevated"), (r"\bfrequently\b", "often"), (r"\bgiven\b", "used"), (r"\bnot pregnant\b", "nonpregnant"), (r"\bunder\b", "below")]
_UP = re.compile(r"\b(?:increas\w*|rais(?:e|es|ed|ing)|elevat\w*|hyper\w+)\b")
_DOWN = re.compile(r"\b(?:decreas\w*|reduc(?:e|es|ed|ing)|lower(?:s|ed|ing)|hypo\w+)\b")
_PROHIB = re.compile(r"not recommended|contraindicat|\bavoid|discontinu|should not|must not|not be (?:used|started|given)|not receive|\bnever\b|withh?old|no longer")
_PERMIT = re.compile(r"\bcan (?:often )?be used|\bmay be used|\brecommended\b|\bsafe\b|\bpreferred\b|\bappropriate\b|\badvised\b|\bcontinued?\b")
_LT = re.compile(r"(?:below|less than|lower than|<|at most)\s*(\d+(?:\.\d+)?)")
_GT = re.compile(r"(?:above|over|greater than|more than|>|at least|exceeds?)\s*(\d+(?:\.\d+)?)")
_OR_GT = re.compile(r"(\d+(?:\.\d+)?)[^.,;]{0,30}?\bor (?:higher|above|more|greater)")
_OR_LT = re.compile(r"(\d+(?:\.\d+)?)[^.,;]{0,30}?\bor (?:lower|below|less)")
_TIMES = re.compile(r"(\d+(?:\.\d+)?) times")
_RANGE = re.compile(r"(\w+) (?:is|are|covers) (\d+) to (\d+)")
_CLASSES = ("cephalosporin", "penicillin", "nsaid", "anticoagulant", "benzodiazepine", "sulfonamide", "statin", "antihistamine")
_DISC = re.compile(r"discontinu|contraindicat|avoid")


def _norm(t: str) -> str:
    t = t.lower()
    for w, n in _NUMWORDS.items():
        t = re.sub(rf"\b{w}\b", str(n), t)
    for pat, rep in _SYN:
        t = re.sub(pat, rep, t)
    return t


def _pairs(t: str) -> set:
    return ({("lt", n) for n in _LT.findall(t)} | {("gt", n) for n in _GT.findall(t)} | {("gt", n) for n in _OR_GT.findall(t)} | {("lt", n) for n in _OR_LT.findall(t)})


def _pol(t: str):
    return "prohibit" if _PROHIB.search(t) else ("permit" if _PERMIT.search(t) else None)


def _dirs(t: str) -> set:
    return ({"up"} if _UP.search(t) else set()) | ({"down"} if _DOWN.search(t) else set())


class EvidenceLinker:
    """Binds EvidenceRef spans to atomic claims via lexical entailment (NOT an NLI model).

    Checks, all of which must pass: numbers (incl. number words) and 'N times' multipliers; comparator+number pairs
    (below/above/at least/or higher); range labels (G3a vs G3b); comparator and predicate in the SAME clause; drug and
    drug-class entities; polarity (prohibit vs permit); direction (increase vs decrease); content-token overlap.
    Measured on eval/scenarios/linker_pairs.yaml (see docs/results.md). Still fooled by subtle scope/quantifier changes.
    """

    def __init__(self, min_overlap: float = 0.5, strict: bool = False):
        """strict=True (use for LLM-originated text): every content word (5+ letters) of the claim must occur in the evidence
        span, so a claim can only restate evidence, never introduce novel wording ('routinely', 'desensitisation', 'high' vs 'low')."""
        self.min_overlap = min_overlap
        self.strict = strict
        self.dk = get_drug_knowledge()

    def entails(self, assertion: str, snippet: str) -> bool:
        a, s = _norm(assertion), _norm(snippet)
        for n in _NUM.findall(a):
            if not re.search(r"(?<![\d.])" + re.escape(n) + r"(?![\d])", s):
                return False
        if any(m not in _TIMES.findall(a) for m in _TIMES.findall(s)):
            return False                                   # dropped multiplier ("above the ULN" != "above 3 times the ULN")
        sp = _pairs(s)
        if any(p not in sp for p in _pairs(a)):
            return False
        sr = {(m[0], m[1], m[2]) for m in _RANGE.findall(s)}
        for lab, x, y in _RANGE.findall(a):
            if (x, y) in {(b, c) for _, b, c in sr} and (lab, x, y) not in sr:
                return False                               # right numbers, wrong label
        clauses = re.split(r",? and |;|, ", s)
        if len(clauses) > 1 and len(re.split(r",? and |;|, ", a)) == 1:   # only for single-clause claims (a verbatim multi-clause claim must pass)
            for p in _pairs(a):
                cx = {i for i, c in enumerate(clauses) if p[1] in c and (_LT if p[0] == "lt" else _GT).search(c)}
                cp = {i for i, c in enumerate(clauses) if _DISC.search(c)}
                if _DISC.search(a) and cx and cp and not (cx & cp):
                    return False                           # threshold and action live in different clauses
        clean_a, clean_s = re.sub(r"[^a-z0-9\- ]", " ", a), re.sub(r"[^a-z0-9\- ]", " ", s)
        for name, pat in self.dk._patterns:
            if pat.search(clean_a):
                generic = self.dk.aliases.get(name, name)
                classes = self.dk.classes_of(generic)
                if not (pat.search(clean_s) or generic in s or any(c.replace("_", " ") in s or c.split("_")[0] in s for c in classes)):
                    return False
        for c in _CLASSES:
            if c in a and c not in s:
                return False
        for w in _PRED:
            if w in a and w not in s:
                return False
        pa, ps = _pol(a), _pol(s)
        if pa and ps and pa != ps:
            return False
        da, ds = _dirs(a), _dirs(s)
        if da and ds and da != ds:
            return False
        ta = set(tokenize(a))
        if self.strict and {t for t in ta if len(t) >= 5} - set(tokenize(s)):
            return False
        return bool(ta) and len(ta & set(tokenize(s))) / len(ta) >= self.min_overlap

    def is_contradiction(self, assertion: str, snippet: str) -> bool:
        """Detects if a claim directly contradicts an evidence snippet (e.g., claiming permission when prohibited or threshold mismatch)."""
        a, s = _norm(assertion), _norm(snippet)
        pa, ps = _pol(a), _pol(s)
        
        # Check entity match
        clean_a, clean_s = re.sub(r"[^a-z0-9\- ]", " ", a), re.sub(r"[^a-z0-9\- ]", " ", s)
        entity_match = False
        for name, pat in self.dk._patterns:
            if pat.search(clean_a) and (pat.search(clean_s) or self.dk.aliases.get(name, name) in s):
                entity_match = True
                break
        
        if not entity_match:
            return False
            
        # Polarity conflict: one permits while the other prohibits
        if pa and ps and pa != ps:
            return True
            
        # Numeric threshold conflict (e.g. claim says contraindication at 45 while evidence says 30)
        pa_pairs, ps_pairs = _pairs(a), _pairs(s)
        if pa_pairs and ps_pairs and pa_pairs != ps_pairs:
            if _PROHIB.search(a) and _PROHIB.search(s):
                return True

        return False

    def verify_claim(self, claim: ClinicalClaim, evidence_pool: List[EvidenceRef]) -> Tuple[ClaimStatus, List[EvidenceRef], str]:
        """Performs a full NLI verification check on a claim against the evidence pool."""
        hits = [e for e in evidence_pool if self.entails(claim.assertion, e.snippet)]
        if hits:
            return ClaimStatus.SUPPORTED, hits, "Entailed by retrieved evidence."
            
        contradictions = [e for e in evidence_pool if self.is_contradiction(claim.assertion, e.snippet)]
        if contradictions:
            return ClaimStatus.UNSUPPORTED, [], f"Claim contradicts retrieved evidence from {contradictions[0].id}."
            
        return ClaimStatus.UNSUPPORTED, [], "No evidence span entails this claim."

    def link(self, claims: List[ClinicalClaim], evidence_pool: List[EvidenceRef]) -> List[ClinicalClaim]:
        out = []
        for c in claims:
            status, hits, rationale = self.verify_claim(c, evidence_pool)
            out.append(c.model_copy(update={"evidence": hits, "status": status}))
        return out
