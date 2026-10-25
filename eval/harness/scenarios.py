"""Scenario loading + seeded, decision-irrelevant covariate variation (so seeds genuinely differ)."""
from __future__ import annotations

import copy
import random
from pathlib import Path
from typing import Any, Dict, List, Tuple

import yaml

from src.contracts.patient import PatientState
from src.ingest.builders import build_state

SC_DIR = Path(__file__).resolve().parents[1] / "scenarios"


def load_adaptation(suite: str = "core") -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """suite: 'core' (adaptation.yaml) or 'holdout' (holdout_adaptation.yaml, written before being run)."""
    f = {"core": "adaptation.yaml", "holdout": "holdout_adaptation.yaml"}[suite]
    d = yaml.safe_load((SC_DIR / f).read_text(encoding="utf-8"))
    return d["scenarios"], d["negative_control"]


def _bs(pid: str, spec: Dict[str, Any], t: str, version: int, extra_labs: Dict[str, float]) -> PatientState:
    labs = {**spec["labs"], **extra_labs}
    conds = [tuple(c) if isinstance(c, list) else c for c in spec["conditions"]]
    return build_state(pid, spec["birth_date"], spec["gender"], timestamp=spec[t], version=version, encounter_id=f"ENC-{version}",
                       conditions=conds, meds=spec["meds"], allergies=spec["allergies"], labs=labs, vitals=spec["vitals"])


def build_pair(scn: Dict[str, Any], seed: int) -> Tuple[PatientState, PatientState]:
    """(prev, curr). Seed varies decision-irrelevant covariates (Na, Hb, SBP) and repeats them with assay-scale noise."""
    rng = random.Random(f"{scn['id']}-{seed}")
    na, hb, sbp = rng.uniform(137, 143), rng.uniform(12.0, 15.0), rng.uniform(112, 124)
    prev, curr = copy.deepcopy(scn["prev"]), copy.deepcopy(scn["curr"])
    for sp in (prev, curr):
        sp["vitals"] = {**sp["vitals"], "SBP": round(sbp + rng.uniform(-3, 3), 0)} if "SBP" in sp["vitals"] and scn["id"][0] != "k" else sp["vitals"]
    pid = f"SC-{scn['id']}-{seed}"
    p = _bs(pid, prev, "prev_time", 1, {"Sodium": round(na, 1), "Hemoglobin": round(hb, 1)})
    c = _bs(pid, curr, "curr_time", 2, {"Sodium": round(na + rng.uniform(-0.8, 0.8), 1), "Hemoglobin": round(hb + rng.uniform(-0.2, 0.2), 1)})
    return p, c
