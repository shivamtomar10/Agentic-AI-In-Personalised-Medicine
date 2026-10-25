"""Stable, resolvable evidence chunks (AGENTS.md Rule 4). Sentence-level chunks with char spans."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional

from src.config import get_settings
from src.contracts.evidence import EvidenceRef, SourceType

_SPLIT = re.compile(r"(?<=[a-z0-9\)])\.\s+(?=[A-Z])")


@dataclass(frozen=True)
class Chunk:
    ref: EvidenceRef
    doc_id: str
    tags: tuple
    title: str
    text: str


def parse_pub_date(s: Optional[str]) -> Optional[date]:
    if not s:
        return None
    parts = [int(p) for p in s.split("-")]
    return date(parts[0], parts[1] if len(parts) > 1 else 1, parts[2] if len(parts) > 2 else 1)


class EvidenceStore:
    def __init__(self, path: Optional[Path] = None):
        path = path or (get_settings().knowledge_dir / "evidence_corpus.jsonl")
        self.chunks: Dict[str, Chunk] = {}
        self.order: List[str] = []
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            d = json.loads(line)
            text = d["text"]
            pos = 0
            for n, sent in enumerate(_SPLIT.split(text)):
                start = text.index(sent, pos)
                end = start + len(sent) + (1 if not sent.endswith(".") and text[start + len(sent):start + len(sent) + 1] == "." else 0)
                snippet = text[start:end]
                pos = end
                cid = f"{d['id']}#{n}"
                ref = EvidenceRef(id=cid, source=SourceType(d["source"]), title=d["title"], url=d.get("url"),
                                  chunk_id=cid, char_span=(start, end), retrieval_score=1.0,
                                  publication_date=d.get("publication_date"), snippet=snippet, verbatim=d.get("verbatim", False))
                self.chunks[cid] = Chunk(ref, d["id"], tuple(d["tags"]), d["title"], text)
                self.order.append(cid)

    def get(self, chunk_id: str) -> Optional[Chunk]:
        return self.chunks.get(chunk_id)

    def by_topic(self, topic: str) -> List[EvidenceRef]:
        return [self.chunks[c].ref for c in self.order if topic in self.chunks[c].tags]

    def resolve(self, ref: EvidenceRef) -> bool:
        """A corpus citation is resolvable iff the id exists and the snippet is really that source span."""
        ch = self.chunks.get(ref.id)
        return ch is not None and ref.snippet.strip() == ch.ref.snippet.strip()

    def is_stale(self, ref: EvidenceRef, today: Optional[date] = None, horizon_years: Optional[float] = None) -> bool:
        d = parse_pub_date(ref.publication_date)
        if d is None:
            return False
        today = today or date.today()
        horizon = horizon_years if horizon_years is not None else get_settings().evidence_horizon_years
        return (today - d).days / 365.25 > horizon


@lru_cache(maxsize=1)
def get_evidence_store() -> EvidenceStore:
    return EvidenceStore()
