"""Precision/recall of the evidence linker on labelled pairs (dev/test split)."""
from __future__ import annotations

from pathlib import Path
from typing import Dict

import yaml

from src.knowledge import get_evidence_store
from src.provenance import EvidenceLinker

F = Path(__file__).resolve().parents[1] / "scenarios" / "linker_pairs.yaml"


def evaluate(min_overlap: float = 0.5, strict: bool = False) -> Dict[str, Dict]:
    data = yaml.safe_load(F.read_text(encoding="utf-8"))
    st, lk, out = get_evidence_store(), EvidenceLinker(min_overlap, strict=strict), {}
    for split in ("dev", "test", "test2"):
        tp = fp = fn = tn = 0
        errs = []
        for cid, claim, label in data[split]:
            pred = lk.entails(claim, st.get(cid).ref.snippet)
            tp += pred and label; fp += pred and not label; fn += (not pred) and label; tn += (not pred) and not label
            if pred != label:
                errs.append(("FP" if pred else "FN", cid, claim))
        out[split] = {"n": len(data[split]), "precision": round(tp / (tp + fp), 3) if tp + fp else None, "recall": round(tp / (tp + fn), 3) if tp + fn else None,
                      "false_positive_rate": round(fp / (fp + tn), 3) if fp + tn else None, "tp": tp, "fp": fp, "fn": fn, "tn": tn, "errors": errs}
    return out


if __name__ == "__main__":
    for k, v in evaluate().items():
        print(k, {a: b for a, b in v.items() if a != "errors"})
        for e in v["errors"]:
            print("   ", e)
