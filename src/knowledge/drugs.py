"""Drug-name normalisation, class lookup, allergy and interaction tables.
Implements the local interaction table required by Part C.4 (DDInter-style, prototype scale)."""
from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from itertools import combinations
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

import yaml

from src.config import get_settings


@dataclass(frozen=True)
class Interaction:
    drug_a: str
    drug_b: str
    severity: str
    effect: str
    topic: str


class DrugKnowledge:
    def __init__(self, path: Optional[Path] = None):
        path = path or (get_settings().knowledge_dir / "drug_classes.yaml")
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        self.aliases: Dict[str, str] = data["aliases"]
        self.drugs: Dict[str, Set[str]] = {k: set(v) for k, v in data["drugs"].items()}
        self.allergy_map: Dict[str, dict] = data["allergy_map"]
        self.rules: List[dict] = data["interaction_rules"]
        names = sorted(set(self.drugs) | set(self.aliases), key=len, reverse=True)
        self._patterns = [(n, re.compile(r"(?<![a-z])" + re.escape(n).replace(r"\-", r"[- ]") + r"(?![a-z])")) for n in names]

    def normalise(self, text: str) -> str:
        t = re.sub(r"[^a-z0-9\- ]", " ", (text or "").lower())
        for name, pat in self._patterns:
            if pat.search(t):
                return self.aliases.get(name, name)
        m = re.match(r"\s*([a-z][a-z\-]+)", t)
        return m.group(1) if m else t.strip()

    def classes_of(self, generic: str) -> Set[str]:
        return set(self.drugs.get(generic, set()))

    def in_class(self, generic: str, cls: str) -> bool:
        return cls in self.drugs.get(generic, set())

    def is_known(self, generic: str) -> bool:
        return generic in self.drugs

    def allergy_conflict(self, allergy_substance: str, med: str) -> Optional[str]:
        """Returns 'definite' | 'possible' | None for an allergy vs a (normalised) medication."""
        sub = (allergy_substance or "").lower()
        med_classes = self.classes_of(med)
        for key, entry in self.allergy_map.items():
            if key in sub:
                if set(entry["definite"]) & med_classes or med == key:
                    return "definite"
                if set(entry["possible"]) & med_classes:
                    return "possible"
        if self.normalise(sub) == med:
            return "definite"
        return None

    def _matches(self, token: str, med: str) -> bool:
        return med == token or token in self.classes_of(med)

    def interactions(self, meds: List[str]) -> List[Interaction]:
        out: List[Interaction] = []
        seen: Set[Tuple[str, str, str]] = set()
        for x, y in combinations(sorted(set(meds)), 2):
            for r in self.rules:
                for a, b in ((x, y), (y, x)):
                    if self._matches(r["a"], a) and self._matches(r["b"], b):
                        key = (x, y, r["topic"])
                        if key not in seen:
                            seen.add(key)
                            out.append(Interaction(x, y, r["severity"], r["effect"], r["topic"]))
        return out

    def duplicate_therapies(self, meds: List[str]) -> List[Tuple[str, List[str]]]:
        """Returns a list of (class_name, [med1, med2...]) for redundant multi-drug prescribing in narrow classes."""
        DUPLICATE_CHECK_CLASSES = ["ace_inhibitor", "arb", "nsaid", "statin", "ssri", "fibrate", "loop_diuretic", "benzodiazepine", "sulfonylurea"]
        out = []
        for cls in DUPLICATE_CHECK_CLASSES:
            in_cls = sorted({m for m in meds if cls in self.classes_of(m)})
            if len(in_cls) > 1:
                out.append((cls, in_cls))
        return out

    def is_known_interaction(self, a: str, b: str) -> bool:
        return bool(self.interactions([a, b]))


@lru_cache(maxsize=1)
def get_drug_knowledge() -> DrugKnowledge:
    return DrugKnowledge()


# Registry of (drug-or-class, condition_key) contraindications/cautions the prototype knows.
# The ValidationAgent uses it to refute "plausible-but-wrong" contraindication claims.
KNOWN_CONTRAINDICATIONS: Set[Tuple[str, str]] = {
    ("metformin", "egfr<30"), ("metformin", "egfr<45"),
    ("raas_inhibitor", "pregnancy"), ("raas_inhibitor", "potassium>5.5"),
    ("warfarin", "pregnancy"), ("teratogenic", "pregnancy"),
    ("nsaid", "egfr<30"), ("nsaid", "interaction:anticoagulant"),
    ("statin", "interaction:fibrate"), ("nsaid", "interaction:ssri"),
    ("ace_inhibitor", "interaction:arb"), ("methotrexate", "interaction:nsaid"),
    ("penicillin", "allergy:penicillin"), ("cephalosporin", "allergy:penicillin"),
    ("hepatotoxic", "alt>3xuln"), ("beers_avoid", "age>=65"), ("beers_caution", "age>=65"),
    ("sglt2_inhibitor", "egfr<20"),
}
