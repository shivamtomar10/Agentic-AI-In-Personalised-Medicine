"""Versioned, content-addressed, append-only PatientStateStore. Implements Part B.2 & C.2."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.contracts.patient import PatientState


class IntegrityError(RuntimeError):
    pass


def _flatten(prefix: str, obj: Any, out: Dict[str, Any]) -> None:
    if isinstance(obj, dict):
        for k, v in obj.items():
            _flatten(f"{prefix}.{k}" if prefix else str(k), v, out)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            _flatten(f"{prefix}[{i}]", v, out)
        if not obj:
            out[prefix] = []
    else:
        out[prefix] = obj


class PatientStateStore:
    """Append-only; every version is hash-chained to its predecessor."""

    def __init__(self, backend_url: Optional[str] = None, persist_path: Optional[str] = None):
        self.backend_url = backend_url  # reserved (Postgres not implemented)
        self._data: Dict[str, List[PatientState]] = {}
        self._by_hash: Dict[str, PatientState] = {}
        self._persist = Path(persist_path) if persist_path else None
        if self._persist and self._persist.exists():
            for line in self._persist.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    st = PatientState.model_validate_json(line)
                    self._data.setdefault(st.patient_id, []).append(st)
                    self._by_hash[st.state_hash] = st

    def append_state(self, state: PatientState) -> PatientState:
        """Assign next version + chain hash. Returns the sealed (immutable) copy that was stored."""
        hist = self._data.setdefault(state.patient_id, [])
        prev = hist[-1] if hist else None
        sealed = state.model_copy(deep=True, update={
            "version": len(hist) + 1,
            "previous_state_hash": prev.state_hash if prev else None,
            "state_hash": None,
        })
        sealed = sealed.model_copy(update={"state_hash": sealed.content_hash()})
        hist.append(sealed)
        self._by_hash[sealed.state_hash] = sealed
        if self._persist:
            self._persist.parent.mkdir(parents=True, exist_ok=True)
            with open(self._persist, "a", encoding="utf-8") as f:
                f.write(sealed.model_dump_json() + "\n")
        return sealed.model_copy(deep=True)

    def append(self, state: PatientState) -> str:
        return self.append_state(state).state_hash  # type: ignore[return-value]

    def get_version(self, patient_id: str, version: int) -> Optional[PatientState]:
        hist = self._data.get(patient_id, [])
        return hist[version - 1].model_copy(deep=True) if 1 <= version <= len(hist) else None

    def get_by_hash(self, h: str) -> Optional[PatientState]:
        s = self._by_hash.get(h)
        return s.model_copy(deep=True) if s else None

    def latest(self, patient_id: str) -> Optional[PatientState]:
        hist = self._data.get(patient_id, [])
        return hist[-1].model_copy(deep=True) if hist else None

    def history(self, patient_id: str) -> List[PatientState]:
        return [s.model_copy(deep=True) for s in self._data.get(patient_id, [])]

    def patients(self) -> List[str]:
        return sorted(self._data)

    def verify_chain(self, patient_id: str) -> bool:
        prev_hash = None
        for s in self._data.get(patient_id, []):
            if not s.verify() or s.previous_state_hash != prev_hash:
                raise IntegrityError(f"Hash chain broken at {patient_id} v{s.version}")
            prev_hash = s.state_hash
        return True

    def diff(self, v1: PatientState, v2: PatientState) -> Dict[str, Any]:
        """Raw field-level differences {path: (old, new)} ignoring bookkeeping fields."""
        skip = {"timestamp", "state_hash", "previous_state_hash", "version", "encounter_id"}
        a: Dict[str, Any] = {}
        b: Dict[str, Any] = {}
        _flatten("", {k: v for k, v in v1.model_dump(mode="json").items() if k not in skip}, a)
        _flatten("", {k: v for k, v in v2.model_dump(mode="json").items() if k not in skip}, b)
        return {k: (a.get(k), b.get(k)) for k in sorted(set(a) | set(b)) if a.get(k) != b.get(k)}
